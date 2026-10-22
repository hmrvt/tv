"""Export robot sprites as large transparent PNGs for use as Seedance references."""
import os
import sys

from PIL import Image

sys.path.insert(0, os.getcwd())
from pixel_sprites import ROBOT_SPRITES, DEFAULT_PALETTE, FACE_VARIANTS

OUT = os.path.join(os.getcwd(), "docs", "refs")


def _rgb(hexstr):
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def render(sprite, scale=24, palette=None, face=None):
    pal = palette or DEFAULT_PALETTE
    rows, cols = len(sprite), len(sprite[0])
    img = Image.new("RGBA", (cols, rows), (0, 0, 0, 0))
    px = img.load()
    for y, row in enumerate(sprite):
        for x, ch in enumerate(row):
            if ch == "." or ch not in pal:
                continue
            px[x, y] = _rgb(pal[ch]) + (255,)
    # Optional face overlay: 12 wide x 6 tall at rows 7-12, cols 4-15
    if face:
        for fy, frow in enumerate(face):
            for fx, ch in enumerate(frow):
                if ch in pal:
                    px[4 + fx, 7 + fy] = _rgb(pal[ch]) + (255,)
    return img.resize((cols * scale, rows * scale), Image.NEAREST)


def main():
    os.makedirs(OUT, exist_ok=True)
    made = []
    # One canonical pose per activity used by the five scenes
    for act in ("idle", "sleep", "eat", "fix", "cheer", "wave", "dance"):
        img = render(ROBOT_SPRITES[act][0])
        path = os.path.join(OUT, f"robot_{act}.png")
        img.save(path)
        made.append((path, img.size))
    # Accent-colour variants of the idle pose, to pin the palette
    for name, accent in (("teal", "#4ECDC4"), ("pink", "#FF6B9D"),
                         ("lavender", "#A18CD1")):
        pal = dict(DEFAULT_PALETTE)
        pal["G"] = accent
        img = render(ROBOT_SPRITES["idle"][0], palette=pal,
                     face=FACE_VARIANTS["happy"])
        path = os.path.join(OUT, f"robot_idle_{name}.png")
        img.save(path)
        made.append((path, img.size))
    for path, size in made:
        print(f"{os.path.basename(path)}  {size[0]}x{size[1]}")
    print(f"\n{len(made)} files in {OUT}")


if __name__ == "__main__":
    main()
