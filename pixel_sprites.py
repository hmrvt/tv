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
