"""Pixel-art sprite data and renderer for Toddler TV robots.

Palette codes
-------------
W  #e8ecf2  body (off-white)          constant
S  #b8c2d0  body shadow               constant
K  #2a3142  outline                   constant
J  #5a6478  joints + boots            constant
F  #0d2540  face screen (dark panel)  constant
G  #4ECDC4  accent: eyes, smile, etc. per-agent override
M  #FFD93D  antenna tip + status LED  constant
R  #FF6B6B  warning LED               constant
.  (space)  transparent               — do not draw
"""

# ── Palette ───────────────────────────────────────────────────────────────────

DEFAULT_PALETTE = {
    "W": "#e8ecf2",
    "S": "#b8c2d0",
    "K": "#2a3142",
    "J": "#5a6478",
    "F": "#0d2540",
    "G": "#4ECDC4",
    "M": "#FFD93D",
    "R": "#FF6B6B",
}

# ── Idle sprite — 20 columns × 24 rows ───────────────────────────────────────

ROBOT_IDLE = [
    "...M............M...",  #  0  antenna tips
    "...M............M...",  #  1
    "...G............G...",  #  2  antenna shafts (accent)
    "...G............G...",  #  3
    "...KKKWWWWWWWWWKKK..",  #  4  head top
    "..KWWWWWWWWWWWWWWK..",  #  5
    ".KWWKKKKKKKKKKKKWWK.",  #  6  face panel bezel
    ".KWKFFFFFFFFFFFFKWK.",  #  7  face screen top
    ".KWKFFGFFFFFFGFFKWK.",  #  8  eye row 1 (G = accent eyes)
    ".KWKFFGFFFFFFGFFKWK.",  #  9  eye row 2
    ".KWKFFFFGFFGFFFFKWK.",  # 10  mouth row 1
    ".KWKFFFFFGGFFFFFKWK.",  # 11  mouth row 2 (G = accent smile)
    ".KWKFFFFFFFFFFFFKWK.",  # 12  face screen bottom
    ".KWWKKKKKKKKKKKKWWK.",  # 13  head bottom
    "..KWWWWWWWWWWWWWWK..",  # 14
    "...KKWWWWWWWWWWKK...",  # 15  neck
    ".JJWWWWSWGGWSWWWWJJ.",  # 16  shoulders + chest port (G = accent)
    ".JJWWWSSWGGWSSWWWJJ.",  # 17
    "..KWWWWWWWWWWWWWWK..",  # 18  body
    "..KWWSSSWMWRWGWWWWK.",  # 19  chest LED row: M=yellow, R=red, G=accent
    "..KWWWWWWWWWWWWWWK..",  # 20  body
    "...KKKWWKKKKWWKKK...",  # 21  hip
    ".....KWWK..KWWK.....",  # 22  upper legs
    ".....KJJK..KJJK.....",  # 23  boots (J = joint/boot colour)
]

assert len(ROBOT_IDLE) == 24, f"ROBOT_IDLE must have 24 rows, got {len(ROBOT_IDLE)}"
assert all(len(r) == 20 for r in ROBOT_IDLE), (
    f"All rows of ROBOT_IDLE must be 20 chars wide: {[len(r) for r in ROBOT_IDLE]}"
)


# ── Renderer ──────────────────────────────────────────────────────────────────

def draw_pixel_sprite(canvas, sprite, cx, cy, pixel_size,
                      palette=None, tags="dynamic", flip=False):
    """Draw a sprite (list of equal-length strings) centered at (cx, cy).

    Parameters
    ----------
    canvas      : tk.Canvas
    sprite      : list[str]  — each string is one row of palette codes
    cx, cy      : int        — pixel centre of the sprite on the canvas
    pixel_size  : int        — side length (px) of one sprite pixel
    palette     : dict | None — code→hex-color; falls back to DEFAULT_PALETTE
    tags        : str        — Tkinter item tag; use 'dynamic' for world canvas
    flip        : bool       — mirror horizontally (for left-facing agents)
    """
    pal  = palette if palette is not None else DEFAULT_PALETTE
    rows = len(sprite)
    cols = len(sprite[0]) if rows else 0
    x0   = cx - (cols * pixel_size) // 2
    y0   = cy - (rows * pixel_size) // 2
    for ry, row in enumerate(sprite):
        py = y0 + ry * pixel_size
        for rx, ch in enumerate(row):
            if ch in (".", " "):
                continue
            color = pal.get(ch)
            if color is None:
                continue
            col_idx = (cols - 1 - rx) if flip else rx
            px = x0 + col_idx * pixel_size
            canvas.create_rectangle(
                px, py, px + pixel_size, py + pixel_size,
                fill=color, outline="", tags=tags,
            )


# ── Activity sprite helpers ───────────────────────────────────────────────────

def _variant(base, mods):
    """Return a copy of base (list[str]) with specific rows replaced by mods."""
    s = list(base)
    for row_idx, new_row in mods.items():
        assert len(new_row) == 20, f"Row {row_idx} must be 20 chars, got {len(new_row)!r}"
        s[row_idx] = new_row
    return s


# Shared row constants used across multiple activities
_ARM_R15      = "...KKWWWWWWWWWWKK.JJ"  # right arm hint at row-15 height
_ARM_L15      = "JJ.KKWWWWWWWWWWKK..."  # left arm hint at row-15 height
_ARM_BOTH15   = "JJ.KKWWWWWWWWWWKK.JJ"  # both arms at row-15 height
_ARM_R14      = "...KWWWWWWWWWWWWK.JJ"  # right arm raised to row-14 height
_ARM_L14      = "JJ.KWWWWWWWWWWWWK..."  # left arm raised to row-14 height
_ARM_BOTH14   = "JJ.KWWWWWWWWWWWWK.JJ"  # both arms raised to row-14 height
_NECK         = "...KKWWWWWWWWWWKK..."  # plain neck (no arm pixels)

_LEG_WIDE_22  = "....KWWK....KWWK...."  # both legs spread (wide stance)
_LEG_WIDE_23  = "....KJJK....KJJK...."
_LEG_XWIDE_22 = "...KWWK.....KWWK...."  # extra-wide
_LEG_XWIDE_23 = "...KJJK.....KJJK...."
_LEG_L_22     = ".....KWWK..........."  # left leg only at row 22
_LEG_R_22     = "...........KWWK....."  # right leg only at row 22
_LEG_IDLE_22  = ".....KWWK..KWWK....."  # both legs (idle)
_LEG_IDLE_23  = ".....KJJK..KJJK....."

# Closed-eye / flat-mouth face rows for sleep
_EYE_CLOSED   = ".KWKFFKFFFFFFKFFKWK."  # G→K (eyes shut)
_MOUTH_FLAT1  = ".KWKFFFFKFFKFFFFKWK."  # G→K (neutral line, row 10)
_MOUTH_FLAT2  = ".KWKFFFFFKKFFFFFkwk."  # G→K (neutral line, row 11)

# ── Walk — 4 frames ───────────────────────────────────────────────────────────
# Arms swing opposite to legs; legs alternate long/short to show stride.
# "long" leg = visible KWWK at row 22; "short" = boot only at row 23.

ROBOT_WALK = [
    _variant(ROBOT_IDLE, {                          # F0: L leg forward
        15: _ARM_R15,
        22: _LEG_L_22,
        23: _LEG_IDLE_23,
    }),
    list(ROBOT_IDLE),                               # F1: neutral
    _variant(ROBOT_IDLE, {                          # F2: R leg forward
        15: _ARM_L15,
        22: _LEG_R_22,
        23: _LEG_IDLE_23,
    }),
    list(ROBOT_IDLE),                               # F3: neutral
]

# ── Run — 4 frames ────────────────────────────────────────────────────────────
# Same leg asymmetry as walk but wider stance; frames 1 & 3 show spread
# "airborne" look rather than neutral.

ROBOT_RUN = [
    _variant(ROBOT_IDLE, {                          # F0: L leg driving
        15: _ARM_R15,
        22: "....KWWK............",
        23: _LEG_WIDE_23,
    }),
    _variant(ROBOT_IDLE, {                          # F1: airborne
        22: _LEG_WIDE_22,
        23: _LEG_WIDE_23,
    }),
    _variant(ROBOT_IDLE, {                          # F2: R leg driving
        15: _ARM_L15,
        22: "............KWWK....",
        23: _LEG_WIDE_23,
    }),
    _variant(ROBOT_IDLE, {                          # F3: airborne
        22: _LEG_WIDE_22,
        23: _LEG_WIDE_23,
    }),
]

# ── Wave — 2 frames ───────────────────────────────────────────────────────────
# Left arm raises between row-15 and row-14 heights.

ROBOT_WAVE = [
    _variant(ROBOT_IDLE, {15: _ARM_L15}),           # F0: arm at shoulder
    _variant(ROBOT_IDLE, {14: _ARM_L14, 15: _NECK}),# F1: arm raised higher
]

# ── Eat — 2 frames ────────────────────────────────────────────────────────────
# Right arm rises toward the face, alternating two heights.

ROBOT_EAT = [
    _variant(ROBOT_IDLE, {15: _ARM_R15}),           # F0: arm at shoulder
    _variant(ROBOT_IDLE, {14: _ARM_R14, 15: _NECK}),# F1: arm raised to face
]

# ── Sleep — 2 frames ──────────────────────────────────────────────────────────
# Closed eyes + flat mouth; legs slightly spread (relaxed slump).
# Frame 1 has idle leg positions for the subtle "breathing" shift.

_SLEEP_FACE = {8: _EYE_CLOSED, 9: _EYE_CLOSED,
               10: _MOUTH_FLAT1, 11: _MOUTH_FLAT2}

ROBOT_SLEEP = [
    _variant(ROBOT_IDLE, {**_SLEEP_FACE,
                           22: _LEG_WIDE_22, 23: _LEG_WIDE_23}),
    _variant(ROBOT_IDLE, {**_SLEEP_FACE,
                           22: _LEG_IDLE_22, 23: _LEG_IDLE_23}),
]

# ── Dance — 3 frames ──────────────────────────────────────────────────────────
# Arms swing up/out; hips wiggle via alternating wide/xwide leg stances.

ROBOT_DANCE = [
    _variant(ROBOT_IDLE, {14: _ARM_BOTH14, 15: _NECK,
                           22: _LEG_XWIDE_22, 23: _LEG_XWIDE_23}),
    _variant(ROBOT_IDLE, {15: _ARM_BOTH15,
                           22: _LEG_IDLE_22,  23: _LEG_IDLE_23}),
    _variant(ROBOT_IDLE, {14: _ARM_BOTH14, 15: _NECK,
                           22: _LEG_WIDE_22,  23: _LEG_WIDE_23}),
]

# ── Fix — 2 frames ────────────────────────────────────────────────────────────
# Right arm extends toward the broken TV; slight leg stance for balance.

ROBOT_FIX = [
    _variant(ROBOT_IDLE, {15: _ARM_R15}),
    _variant(ROBOT_IDLE, {14: _ARM_R14, 15: _NECK,
                           22: "....KWWK...KWWK.....",
                           23: "....KJJK...KJJK....."}),
]

# ── Cheer — 2 frames ──────────────────────────────────────────────────────────
# Both arms raised; legs spread wide.

ROBOT_CHEER = [
    _variant(ROBOT_IDLE, {15: _ARM_BOTH15,
                           22: _LEG_WIDE_22, 23: _LEG_WIDE_23}),
    _variant(ROBOT_IDLE, {14: _ARM_BOTH14, 15: _NECK,
                           22: _LEG_XWIDE_22, 23: _LEG_XWIDE_23}),
]

# ── Skate — 2 frames ──────────────────────────────────────────────────────────
# Asymmetric legs: one gliding forward, one pushing back.

ROBOT_SKATE = [
    _variant(ROBOT_IDLE, {                          # F0: glide
        15: _ARM_L15,
        22: _LEG_L_22,
        23: ".....KJJK......KJJK.",
    }),
    _variant(ROBOT_IDLE, {                          # F1: push-off
        22: _LEG_R_22,
        23: "....KJJK....KJJK....",
    }),
]

# ── Carry — 2 frames ──────────────────────────────────────────────────────────
# Both arms held out (carrying a prop); slow step via leg alternation.

ROBOT_CARRY = [
    _variant(ROBOT_IDLE, {15: _ARM_BOTH15,
                           22: _LEG_IDLE_22, 23: _LEG_IDLE_23}),
    _variant(ROBOT_IDLE, {15: _ARM_BOTH15,
                           22: _LEG_WIDE_22, 23: _LEG_WIDE_23}),
]

# ── Master lookup ─────────────────────────────────────────────────────────────

ROBOT_SPRITES = {
    "idle":  [list(ROBOT_IDLE)],
    "walk":  ROBOT_WALK,
    "run":   ROBOT_RUN,
    "wave":  ROBOT_WAVE,
    "eat":   ROBOT_EAT,
    "sleep": ROBOT_SLEEP,
    "dance": ROBOT_DANCE,
    "fix":   ROBOT_FIX,
    "cheer": ROBOT_CHEER,
    "skate": ROBOT_SKATE,
    "carry": ROBOT_CARRY,
}

# Phase increment per frame (controls animation speed).
# Higher = faster frame cycling.
ACTIVITY_FRAME_RATE = {
    "idle":  0.04,
    "walk":  0.18,
    "run":   0.28,
    "wave":  0.20,
    "eat":   0.18,
    "sleep": 0.04,
    "dance": 0.22,
    "fix":   0.18,
    "cheer": 0.22,
    "skate": 0.18,
    "carry": 0.10,
}

# Validate every frame in every activity at import time
import math as _math
_TWO_PI = 2 * _math.pi
for _act, _frames in ROBOT_SPRITES.items():
    for _fi, _frame in enumerate(_frames):
        assert len(_frame) == 24, \
            f"{_act}[{_fi}] must have 24 rows, got {len(_frame)}"
        for _ri, _row in enumerate(_frame):
            assert len(_row) == 20, \
                f"{_act}[{_fi}] row {_ri} must be 20 chars, got {len(_row)!r}"
del _act, _frames, _fi, _frame, _ri, _row
