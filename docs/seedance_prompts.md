# Seedance 2.5 Prompt Pack — Toddler TV

Video-generation prompts for the five full-screen off-air scenes (`RobotCanvas`),
plus image prompts for the app / web-remote button icons.

**Scope note:** the panoramic cityscape (`RobotWorldCanvas` — `_draw_buildings`,
`_draw_road`, `_draw_cars`) is out of scope, and every prompt excludes it
explicitly.

> **Don't paste this file into Seedance.** It's a reference. The ready-to-paste
> prompts are one file per scene in [`docs/prompts/`](prompts/), each with its
> settings in a comment header. Regenerate them after editing any shared section
> with `.venv/Scripts/python.exe tools/build_prompts.py`.

---

## 0. What 2.5 changes

2.5 is a different prompting discipline from 1.x, not just a better model. Five
things matter for this project:

| | 1.x | 2.5 |
|---|---|---|
| Prompt shape | one flowing paragraph | **labeled sections** — skip a section and output degrades |
| Camera lock | `--camerafixed true` | **no such parameter** — lock it in prose with rig/lens specs |
| Exclusions | negatives backfire, phrase positively | **explicit `No X anywhere` works** in GLOBAL STYLE / AUDIO |
| Duration | 5 s / 10 s | **4–30 s**, default 5 |
| Audio | none | **`generate_audio` defaults to `true`** |
| On-screen text | melts | **holds letter by letter through motion** |
| References | none | **up to 50 inputs** (30 images, 10 video, 10 audio), addressed by position |

### The four that will bite you

**1. Audio is on by default.** Left alone, 2.5 will score your sleeping scene with
invented ambient music or voices, and it will play in a child's bedroom at 19:30.
Set `generate_audio: false` on every request, *and* put an AUDIO exclusion section
in the prompt, *and* strip the track in post with `ffmpeg -an`. Three layers,
because this one is genuinely unpleasant if it slips through.

**2. Double quotes mean dialogue.** 2.5 reads `"..."` as spoken lines and
generates lip-sync and a voice for them. Never use double quotes in a scene
prompt. The prompts below use none.

**3. `camera_fixed` is gone.** This was the single most useful flag in 1.x. In 2.5
you get the same result by specifying the rig mechanically — *locked tripod, fixed
35mm lens, zero camera translation, zero rotation* — because per ByteDance's own
guidance adjectives like "smooth" get read as maximum acceleration, not restraint.
Every prompt below carries an OPTICS section that does this.

**4. Upscaled resolutions destroy pixel art.** The `720p-sr`, `1080p-esr`,
`1440p-sr`, `4k-esr` options run a super-resolution pass that invents detail and
smooths hard edges — exactly wrong here. Use plain `1080p`. Always.

### Parameters

Verified against fal.ai and Atlas Cloud's published schemas; **names differ per
host**, so check yours.

```json
{
  "prompt": "...",
  "image_url": "<your first frame>",
  "duration": 20,
  "resolution": "1080p",
  "generate_audio": false
}
```

- `duration` — `4`–`30`, or `-1`/`auto`. Default 5.
- `resolution` — `480p`, `720p`, `1080p`, plus `-sr`/`-esr` upscale variants. **Use
  `1080p`.**
- `ratio` — on image-to-video this is `adaptive` only; framing comes from your
  input image, so the 16:9 is set by the first frame, not the request.
- `seed` — returned with every result. Note it and reuse it across all five scenes.

### Workflow: image-to-video, with references

Pure text-to-video will drift the robot design across five separate clips. Use:

1. **Generate a first frame** per scene with an image model, using the FIRST FRAME
   prompts in §3.
2. **Attach sprite references.** `tools/export_sprite_refs.py` renders your actual
   sprites from `pixel_sprites.py` as large transparent PNGs into `docs/refs/`:

   ```bash
   .venv/Scripts/python.exe tools/export_sprite_refs.py
   ```

   Ten files — `robot_idle`, `robot_sleep`, `robot_eat`, `robot_fix`,
   `robot_cheer`, `robot_wave`, `robot_dance`, plus teal/pink/lavender accent
   variants at 480×576. Pass the poses a scene actually uses as references and
   name them by position in the prompt (*reference 1 is the robot design*). This
   is the strongest consistency lever 2.5 gives you — the model is matching your
   real sprite data rather than a description of it.
3. **Generate at 1080p**, audio off.
4. **Re-quantize in post** (§5). Still not optional.

### Layout constraints (from the live renderer)

- **Action at ~42 % height** — `RobotCanvas` centres scenes at `cy = 0.42 * h`.
- **Bottom 32 % quiet** — `_draw_text` paints label, message and subtext between
  `0.68h` and `0.93h`.
- **Top 7 % quiet** — the 72 px channel bar.
- **Bottom-right ~150×175 px quiet** — the QR overlay at `rely=0.76, anchor="se"`.

### Backplate variant

To keep the live sprites — the per-robot accent/face/accessory randomisation in
`_randomise_robots`, which is what stops the scene going stale — generate
environments only and layer sprites on top. Replace the CHARACTERS section with:

> CHARACTERS: None. The stage is empty of figures. Only environment, props and
> light move.

Worth doing for `sleeping`, which runs roughly eleven hours a day.

---

## 1. Shared sections

These four are identical across all five scenes. Paste them into every prompt.

### GLOBAL STYLE

```
GLOBAL STYLE: Chunky retro pixel-art animation, 1980s cathode-ray television
aesthetic. Hard pixel blocks on a visible fixed grid, flat areas of solid colour,
dithered banding instead of smooth gradients. Faint horizontal scanlines across
the whole frame, gentle neon bloom around bright elements, corners falling into
shadow. 16:9. Constant shutter, no motion blur, no depth of field, no film grain,
no lens flare. Strict palette: deep indigo #0d0721, dark violet #1a0a3e, muted
violet #2a1a4e, neon green #00ff88, teal #4ECDC4, warm yellow #FFD93D, coral red
#FF6B6B. Exclusions, absolute: no buildings anywhere, no city skyline, no
windows, no street, no road, no pavement, no cars, no vehicles, no lamp posts, no
urban horizon. No photorealism, no 3D render, no smooth vector art, no cel-shaded
anime. No logos, no watermark, no subtitles, no captions, no user-interface
overlay.
```

### CHARACTERS

```
CHARACTERS: Two boxy pixel-art robots, matching reference image 1 exactly. Body
of off-white #e8ecf2 panels with grey #b8c2d0 shading and a one-pixel dark navy
#2a3142 outline throughout, grey #5a6478 joints and boots. Square head with a
bevelled bezel framing a dark navy #0d2540 face screen; eyes and smile glow on
that screen in the robot's own accent colour. Two thin antennae rising from the
head with warm yellow #FFD93D tips. Chest plate carrying a small accent-coloured
port and a row of three status LEDs in yellow, coral red and accent colour. Two
stubby legs ending in grey boots. Friendly and rounded, toddler-safe: no teeth,
no weapons, no menace. ROBOT A is the hero, left of centre, one third larger.
ROBOT B is smaller, right of centre. Their proportions, outline weight and face
geometry never change for the length of the clip.
```

### OPTICS

```
OPTICS: Single continuous shot, no cuts, no transitions. Locked tripod, fixed
35mm lens, zero camera translation, zero camera rotation, zero zoom, zero dolly,
zero crane, zero parallax, zero handheld micro-jitter. The frame is mechanically
immobile for the entire duration: every pixel of the background sits at the same
screen coordinate in the last frame as in the first. Orthographic flat-on framing,
no perspective shift.
```

### AUDIO

```
AUDIO: Silent. No music, no score, no narration, no dialogue, no voices, no sound
effects, no ambient room tone, no foley.
```

Also set `generate_audio: false`. The section alone is not enough.

---

## 2. Per-scene sections

Paste the four shared sections above plus the five below, in this order: GLOBAL
STYLE, SCENE, CHARACTERS, LOCATION, FIRST FRAME AND BLOCKING, MOTION, OPTICS,
PHYSICS, LIGHTING, AUDIO.

§4 shows `sleeping` fully assembled, so the pattern is unambiguous.

---

### 2a. `sleeping` — "ROBOTS SLEEPING"

Covers 19:30–06:50 — longer than the other four combined. **Generate this one at
`duration: 30`.** A 5-second loop seen for eleven hours is torture; thirty seconds
of slow drift is watchable. Dark, low-contrast, nothing bright.

```
SCENE: Two small robots doze under a night sky while a crescent moon glows
overhead. Nothing happens. The mood is hushed, slow and safe, a lullaby.

LOCATION: A near-black indigo #0d0721 night sky filling the frame, dusted with
small stars in white, pale blue #aaccff, warm cream #ffeeaa and mint #ccffee, each
a single bright pixel with a one-pixel cross of glow. A darker indigo #0e0825
elliptical pool of shadow behind the robots. Upper right, a crescent moon in warm
yellow #FFD93D, its inner curve bitten out by a disc of sky colour, haloed by
three concentric rings of dim amber, with five tiny yellow sparkle-stars orbiting
at a distance. Beneath the robots, a plain flat unbroken band of dark violet
ground running to both frame edges, and empty negative space filling the lower
third of the frame.

FIRST FRAME AND BLOCKING: ROBOT A lies asleep on its side, left of centre, on a
plump oval pillow of dark violet #2a1a4e outlined in thin neon green #00ff88, its
face screen showing two flat horizontal dashes for closed eyes and a small soft
curve of a mouth in teal, antenna tips dimmed to low amber. ROBOT B sits upright
right of centre in lavender accent, drowsy but awake, one eye narrower than the
other, head tilted toward its sleeping friend. Both heads sit at 42 percent of the
frame height. Neither robot changes position on screen for the whole clip.

MOTION: Continuous, cyclic, uneventful. Stars twinkle softly out of phase with one
another at roughly one cycle per second each. Both robots breathe with a slow
three-pixel vertical rise and fall in opposite phase, one settling as the other
lifts, about twelve full cycles across the clip. Neon green lowercase z glyphs
drift up and to the right from ROBOT A head, one at a time, swaying gently
sideways and fading out near the top of frame, a new one beginning as the previous
one vanishes. The warm yellow sparkles beside ROBOT B orbit slowly around its
head. The amber rings around the moon pulse almost imperceptibly. At 28s the
scene returns to exactly the arrangement of the first frame, so the clip can loop.

PHYSICS: Motion is gentle and weightless. The z glyphs rise at constant slow speed
with a slight sinusoidal sideways sway, never accelerating. The pillow does not
compress or deform. Nothing falls, nothing collides, nothing is disturbed by wind.

LIGHTING: Lit only by the moon and by the robots own face screens and status LEDs,
so the off-white bodies read cool grey-violet rather than white. Very low contrast
throughout, deep shadow in the corners, no light source bright enough to wake a
sleeping child. Lighting is constant, with no flicker and no shift in colour
temperature.
```

---

### 2b. `lunch` — "LUNCH BREAK"

11:00–12:30. The brightness outlier — generate it last, once the other four have
set your baseline. `duration: 10`.

```
SCENE: Two robots share a quiet meal at a low table under a bright midday sky.
Warm, domestic, unhurried.

LOCATION: A three-band dithered sky, deeper blue #5ba3d9 across the top third
stepping down to light sky blue #87CEEB below, with visible pixel dithering at the
band edges rather than a smooth blend. A warm yellow #FFD93D sun disc at upper
right inside three rings of dim amber. Three pale blue-white #deeeff cloud puffs
high in frame, each one large circle flanked by two smaller ones, flat-filled with
a single highlight step. Centre frame, a low table in dark violet #2a1a4e outlined
in thin neon green #00ff88, with a pale highlight line along its front edge and two
square legs. On it, a dark oval plate of #1a0a3e rimmed in neon green holding three
blocky food shapes, one coral red #FF6B6B, one cream #FFD93D, one teal #4ECDC4,
each a plain rectangle of flat colour. To the right, a square-sided violet mug
outlined in neon green with a single-pixel C-shaped handle, filled with teal
liquid. Beneath, a plain flat unbroken band of dark violet ground running to both
frame edges, empty negative space in the lower third.

FIRST FRAME AND BLOCKING: ROBOT A sits at the table left of centre, leaning
slightly forward mid-meal, mint accent, face screen showing two upturned chevron
eyes and a wide smile, right arm raised holding a small neon green three-tined
fork. ROBOT B stands right of centre in orange accent, arms down, content neutral
face, watching its friend eat. Both heads at 42 percent of the frame height.
Neither robot changes position on screen for the whole clip.

MOTION: Continuous and cyclic. Steam rises from the plate and from the mug in slow
curling wisps that thin and fade near the top of their travel, new wisps
continuously replacing them. The three cloud puffs drift very slowly leftward at
constant speed. Both robots bob three pixels vertically in opposite phase. ROBOT A
chews, its mouth shape alternating between a small open square and a closed curve
about twice per second, while its forked hand lifts a few pixels and lowers again
in an unhurried eating rhythm. ROBOT B head turns very slightly toward its friend
and back. The amber rings around the sun pulse faintly. At 9s the scene returns to
the arrangement of the first frame, so the clip can loop.

PHYSICS: Steam rises at constant slow speed with gentle sideways drift, expanding
slightly as it thins. Clouds translate horizontally without deforming. The table,
plate and mug are rigid and never wobble, tip or slide. Nothing spills.

LIGHTING: Flat high-key daylight from the upper right, shadowless and cheerful, so
the robot bodies read bright white-cream with grey #b8c2d0 shading only on their
left sides. Lighting is constant, no flicker, no shift in colour temperature.
```

---

### 2c. `fixing` — "ENGINEERS AT WORK"

Overcast and mechanical. The only scene with genuinely fast motion — the glitch
churn — which makes it the one most likely to tempt the model into a camera move.
Lean on OPTICS. `duration: 10`.

```
SCENE: Two robot engineers repair a broken television set under a flat grey sky.
Busy, industrious, faintly comic.

LOCATION: A dithered two-step overcast sky from slate grey-blue #546e7a across the
top to lighter grey #90a4ae below, no blue and no warmth, deliberately drab. A pale
washed-out disc #b0bec5 of sun barely showing at upper right, its glow rings almost
invisible. Three dull pale cloud puffs high in frame. Centre frame, a broken retro
television set facing the viewer: a chunky outer bezel in dark plum #221133 with a
bold two-pixel coral red #FF6B6B border, enclosing a near-black #0a0010 screen
crossed by evenly spaced dark horizontal scanlines in #110022. Ten small horizontal
glitch bars of harsh neon green #00ff88 scattered across that screen at irregular
positions. A coral red warning triangle at the screen lower edge. Below the set, a
stubby two-tier stand in dark violet #2a1a4e. Beneath, a plain flat unbroken band of
dark violet ground running to both frame edges, empty negative space in the lower
third.

FIRST FRAME AND BLOCKING: ROBOT A stands left of centre leaning in toward the
television at work, sky blue accent, face screen showing two round dot eyes and a
short diagonal slash of a mouth, right arm extended toward the set holding a neon
green wrench with a small circular ring head on a thick angled shaft. ROBOT B
stands right of centre in pink accent supervising, one hand raised in a small wave,
the other holding a clipboard: a dark violet rectangle outlined in neon green with a
darker indigo clip bar across the top and three horizontal neon green ruled lines
below. Both heads at 42 percent of the frame height. Neither robot changes position
on screen for the whole clip.

MOTION: Continuous and cyclic. The neon green glitch bars inside the television
screen scramble to new positions and lengths several times a second, a fast nervous
static churn confined strictly inside the screen rectangle. The coral red warning
triangle pulses about twice a second. Sparks burst from the set upper-left corner in
repeating volleys of five four-pointed glyphs, each arcing up and outward before
vanishing. ROBOT A wrench arm makes a small repeating tightening motion against the
bezel. ROBOT B raised hand waves slowly. Both robots bob two pixels vertically in
opposite phase. At 9s the scene returns to the arrangement of the first frame, so
the clip can loop.

PHYSICS: Sparks launch on ballistic arcs and fade at the top of their travel, never
bouncing and never landing. The television set, its stand, the wrench and the
clipboard are rigid: they never wobble, rotate, tip, stretch or scale. The glitch
churn is a flat screen effect with no depth and casts no light on the robots.

LIGHTING: Cold, diffuse, shadowless overcast light. The only warm light in frame
comes from the sparks and from the robots own status LEDs, and it does not spill
onto the ground. Base lighting is constant, with no flicker except the sparks
themselves.
```

---

### 2d. `kita_goodbye` — "HAVE FUN AT KITA!"

The celebratory scene. **2.5 renders on-screen text reliably now**, so unlike 1.x
you can generate the banner — but verify it frame by frame before you trust it to a
child learning letters, and keep the composite fallback in §5. `duration: 10`.

```
SCENE: Two robots celebrate a cheerful goodbye beneath a huge sunrise, with
bunting and falling confetti. Joyful, bouncy, warm.

LOCATION: A dithered sky from burnt orange #e07a3a across the top to soft golden
sand #f4c47a below, visibly banded. Three pale cloud puffs catching the light high
in frame. Dominating the centre, a large sunrise disc in bright orange #FF8C42
ringed with a three-pixel warm yellow #FFD93D outline and haloed by three
concentric rings of deep ember red, with twelve straight yellow rays radiating at
even thirty-degree intervals, each a different length. Floating above the sun, a
small warm-coloured pixel school backpack with two straps and a front pocket. Six
celebratory pixel motifs orbit the sun in a wide ellipse, evenly spaced: a yellow
four-point star, a yellow heart, a larger sparkle-star, a white twinkle, a mint
green #6BCB77 heart, and a coral red balloon on a single-pixel string. Across the
middle of the frame at 55 percent height, a horizontal celebration banner: a deep
indigo #1a004e rectangle with a hard two-pixel warm yellow #FFD93D border and a
solid dark drop shadow offset three pixels down and right, bearing the words HAVE
FUN AT KITA! in blocky warm-yellow monospace pixel capitals, centred, spelled
exactly that way and held perfectly stable and legible for the entire clip. Above
the banner, a row of small downward-pointing triangular pennants alternating coral
red #FF6B6B, teal #4ECDC4 and warm yellow. Beneath the robots, a plain flat
unbroken band of ground running to both frame edges, empty negative space in the
lower third.

FIRST FRAME AND BLOCKING: ROBOT A stands left of centre cheering with both arms
thrown high above its head, yellow accent, face screen showing two upturned chevron
eyes and a wide open grin. ROBOT B stands right of centre in teal accent waving
farewell with one arm, carrying a small lunchbox in the other: a dark violet
rectangle outlined in teal with a simple divided bento pattern on its face. Both
heads at 42 percent of the frame height. Neither robot changes position on screen
for the whole clip.

MOTION: Continuous and cyclic. The sun twelve rays rotate slowly clockwise while
individually pulsing in length. The six celebratory motifs travel steadily around
their elliptical orbit, completing exactly one circuit across the clip. The school
backpack hovers with a slow ten-pixel rise and fall. The pennant row ripples in a
left-to-right travelling wave. Confetti falls continuously across the whole frame,
each piece tumbling and alternating between a small circle and a short horizontal
bar, entering at the top as others leave the bottom. ROBOT A bounces on the spot
with both arms raised. ROBOT B arm waves in farewell, its lunchbox swinging very
slightly. The banner and its lettering do not move, warp, ripple or change. At 9s
the scene returns to the arrangement of the first frame, so the clip can loop.

PHYSICS: Confetti falls at varied constant speeds with gentle lateral tumble, never
accelerating hard and never piling up on the ground. The pennant wave is a shallow
fabric ripple that does not detach the pennants from their line. The banner is rigid
board, not cloth: it does not sag, flap or curl. The sun does not drift or scale.

LIGHTING: Warm golden directional light from the central sun, throwing warm
highlights along the robots inner edges and cool violet shadow on their outer edges.
Constant, no flicker, no shift in colour temperature.
```

---

### 2e. `tubby` — "TUBBY TIME!"

The most prop-heavy scene, and the widest single object — which makes it the one
most likely to wobble. The PHYSICS section pins the tub hard. `duration: 10`.

```
SCENE: Two robots enjoy a bubble bath in a clawfoot tub under a purple evening
sky. Warm, playful, splashy.

LOCATION: A dithered sky from rich purple #6b3fa0 across the top to soft lilac
#c9a8e0 below, with a muted apricot #f4a261 sun low at upper right inside three
faint glow rings and three pale cloud puffs drifting high. Dominating the frame, a
wide freestanding clawfoot bathtub seen front-on spanning most of the frame width:
body of deep steel blue #1a3850 with a two-pixel teal #4ECDC4 outline, a rolled rim
in slate #253e52 across the top as a flattened ellipse with a pale #3a607e shine
stripe along its upper left, and water inside in dark teal-navy #0a3a55. Four brass
clawfoot legs in warm gold #c8a030 with darker #8b6014 outlines, each a short shaft,
a rounded ball and three small gripping toes. At the right end of the rim a tall
brass faucet, a square spout column with a rounded outlet beneath. At the left end,
two round brass valve knobs, one marked with a coral red #FF6B6B bar and one with a
teal bar. A thick row of pale blue-white #c8e8f8 foam suds along the waterline as
overlapping flattened circles of varied size. Floating centre-right, a large bright
yellow pixel rubber duck with an orange beak and a single dark eye dot. Empty
negative space fills the lower third of the frame.

FIRST FRAME AND BLOCKING: Both robots sit half-submerged in the tub, visible from
the chest up with the foam line cutting across them. ROBOT A is left of centre in
pink accent with both arms raised in delight, face screen showing two upturned
chevron eyes and a wide open grin. ROBOT B is right of centre in lavender accent,
dancing with arms out to either side, star-shaped sparkle eyes and an open grin.
Both heads at 42 percent of the frame height. Neither robot changes position on
screen for the whole clip.

MOTION: Continuous and cyclic. The foam suds undulate along the waterline in a slow
left-to-right travelling wave. Soap bubbles rise continuously from the water surface
as thin lilac #c9a8e0 outlined circles each with a small white highlight arc at upper
left, drifting up past the rim and off the top of frame, new ones spawning at the
waterline as older ones leave. Four soft #c8e8f8 steam wisps drift upward above the
rim and dissipate. The rubber duck bobs gently with a five-pixel rise and fall. Teal
droplets fall from the brass faucet in a repeating chain into the water. Around
ROBOT A, splash droplets burst outward in expanding rings. Both robots bob four
pixels vertically in opposite phase, ROBOT B alternating its arms up and down. At 9s
the scene returns to the arrangement of the first frame, so the clip can loop.

PHYSICS: The bathtub is completely rigid and motionless: its body, rim, four brass
clawfoot legs, faucet and both valve knobs stay at fixed screen coordinates and
never wobble, tilt, stretch, scale or sway. The water surface moves only as the
shallow foam wave; the water level never rises or falls. Bubbles rise at constant
slow speed without popping. Droplets fall in straight lines under gentle gravity and
merge into the water without splashing the tub exterior. No water leaves the tub.

LIGHTING: Soft warm-cool evening light from the upper right, with cool teal bounce
light thrown up onto the robots chins and chests from the water. Constant, no
flicker, no shift in colour temperature.
```

---

## 3. First-frame prompts (image model)

For the I2V conditioning frame. Send to an image model — Seedance can't emit a
still. Build each from its scene's LOCATION plus FIRST FRAME AND BLOCKING sections
above, prefixed with:

```
Chunky retro pixel-art illustration, 16:9, 1920x1080, 1980s CRT television look:
hard pixel blocks on a visible grid, flat colour, dithered banding, faint
scanlines, vignetted corners. Strict palette: #0d0721, #1a0a3e, #2a1a4e, #00ff88,
#4ECDC4, #FFD93D, #FF6B6B. No buildings, no skyline, no road, no cars. No text
except where explicitly described.

[LOCATION section]

[FIRST FRAME AND BLOCKING section]

Robot construction: off-white #e8ecf2 panel bodies with #b8c2d0 shading and a
one-pixel dark navy #2a3142 outline, grey #5a6478 joints and boots, square head
with a bevelled bezel framing a dark navy #0d2540 face screen carrying glowing
accent-coloured eyes and smile, two thin antennae with warm yellow #FFD93D tips, a
chest plate with a row of yellow, coral red and accent status LEDs, two stubby legs
in grey boots.
```

Check each frame against the §0 layout constraints and fix the palette by hand
before you spend video credits on it.

---

## 4. Worked example — `sleeping`, fully assembled

Paste as one continuous block. `duration: 30`, `resolution: 1080p`,
`generate_audio: false`, `image_url` = your sleeping first frame, references =
`docs/refs/robot_sleep.png` and `docs/refs/robot_idle_lavender.png`.

```
GLOBAL STYLE: Chunky retro pixel-art animation, 1980s cathode-ray television
aesthetic. Hard pixel blocks on a visible fixed grid, flat areas of solid colour,
dithered banding instead of smooth gradients. Faint horizontal scanlines across the
whole frame, gentle neon bloom around bright elements, corners falling into shadow.
16:9. Constant shutter, no motion blur, no depth of field, no film grain, no lens
flare. Strict palette: deep indigo #0d0721, dark violet #1a0a3e, muted violet
#2a1a4e, neon green #00ff88, teal #4ECDC4, warm yellow #FFD93D, coral red #FF6B6B.
Exclusions, absolute: no buildings anywhere, no city skyline, no windows, no street,
no road, no pavement, no cars, no vehicles, no lamp posts, no urban horizon. No
photorealism, no 3D render, no smooth vector art, no cel-shaded anime. No logos, no
watermark, no subtitles, no captions, no user-interface overlay.

SCENE: Two small robots doze under a night sky while a crescent moon glows
overhead. Nothing happens. The mood is hushed, slow and safe, a lullaby.

CHARACTERS: Two boxy pixel-art robots, matching reference image 1 exactly. Body of
off-white #e8ecf2 panels with grey #b8c2d0 shading and a one-pixel dark navy #2a3142
outline throughout, grey #5a6478 joints and boots. Square head with a bevelled bezel
framing a dark navy #0d2540 face screen; eyes and smile glow on that screen in the
robot own accent colour. Two thin antennae rising from the head with warm yellow
#FFD93D tips. Chest plate carrying a small accent-coloured port and a row of three
status LEDs in yellow, coral red and accent colour. Two stubby legs ending in grey
boots. Friendly and rounded, toddler-safe: no teeth, no weapons, no menace. ROBOT A
is the hero, left of centre, one third larger. ROBOT B is smaller, right of centre.
Their proportions, outline weight and face geometry never change for the length of
the clip.

LOCATION: A near-black indigo #0d0721 night sky filling the frame, dusted with small
stars in white, pale blue #aaccff, warm cream #ffeeaa and mint #ccffee, each a single
bright pixel with a one-pixel cross of glow. A darker indigo #0e0825 elliptical pool
of shadow behind the robots. Upper right, a crescent moon in warm yellow #FFD93D, its
inner curve bitten out by a disc of sky colour, haloed by three concentric rings of
dim amber, with five tiny yellow sparkle-stars orbiting at a distance. Beneath the
robots, a plain flat unbroken band of dark violet ground running to both frame edges,
and empty negative space filling the lower third of the frame.

FIRST FRAME AND BLOCKING: ROBOT A lies asleep on its side, left of centre, on a plump
oval pillow of dark violet #2a1a4e outlined in thin neon green #00ff88, its face
screen showing two flat horizontal dashes for closed eyes and a small soft curve of a
mouth in teal, antenna tips dimmed to low amber. ROBOT B sits upright right of centre
in lavender accent, drowsy but awake, one eye narrower than the other, head tilted
toward its sleeping friend. Both heads sit at 42 percent of the frame height. Neither
robot changes position on screen for the whole clip.

MOTION: Continuous, cyclic, uneventful. Stars twinkle softly out of phase with one
another at roughly one cycle per second each. Both robots breathe with a slow
three-pixel vertical rise and fall in opposite phase, one settling as the other
lifts, about twelve full cycles across the clip. Neon green lowercase z glyphs drift
up and to the right from ROBOT A head, one at a time, swaying gently sideways and
fading out near the top of frame, a new one beginning as the previous one vanishes.
The warm yellow sparkles beside ROBOT B orbit slowly around its head. The amber rings
around the moon pulse almost imperceptibly. At 28s the scene returns to exactly the
arrangement of the first frame, so the clip can loop.

OPTICS: Single continuous shot, no cuts, no transitions. Locked tripod, fixed 35mm
lens, zero camera translation, zero camera rotation, zero zoom, zero dolly, zero
crane, zero parallax, zero handheld micro-jitter. The frame is mechanically immobile
for the entire duration: every pixel of the background sits at the same screen
coordinate in the last frame as in the first. Orthographic flat-on framing, no
perspective shift.

PHYSICS: Motion is gentle and weightless. The z glyphs rise at constant slow speed
with a slight sinusoidal sideways sway, never accelerating. The pillow does not
compress or deform. Nothing falls, nothing collides, nothing is disturbed by wind.

LIGHTING: Lit only by the moon and by the robots own face screens and status LEDs, so
the off-white bodies read cool grey-violet rather than white. Very low contrast
throughout, deep shadow in the corners, no light source bright enough to wake a
sleeping child. Lighting is constant, with no flicker and no shift in colour
temperature.

AUDIO: Silent. No music, no score, no narration, no dialogue, no voices, no sound
effects, no ambient room tone, no foley.
```

---

## 5. Post-processing

Uses the `ffmpeg.exe` already bundled at `ffmpeg/bin/`.

### Strip audio, always

```bash
ffmpeg/bin/ffmpeg.exe -i raw.mp4 -an -c:v copy muted.mp4
```

Belt and braces alongside `generate_audio: false`. Check with
`ffmpeg/bin/ffprobe.exe -show_streams muted.mp4` that no audio stream survives.

### Build a palette lock file

`Pillow==12.1.0` is already pinned:

```python
from PIL import Image
PALETTE = [
    "#0d0721", "#1a0a3e", "#2a1a4e", "#00ff88", "#4ECDC4", "#FFD93D",
    "#FF6B6B", "#e8ecf2", "#b8c2d0", "#2a3142", "#5a6478", "#0d2540",
    "#87CEEB", "#5ba3d9", "#c8e8f8", "#c8a030",
]
img = Image.new("RGB", (len(PALETTE), 1))
img.putdata([tuple(int(h[i:i+2], 16) for i in (1, 3, 5)) for h in PALETTE])
img.save("docs/palette.png")
```

Add or drop swatches per scene — `lunch` wants the sky blues, `tubby` the brass and
foam, `sleeping` almost none of them.

### Re-quantize the grid and lock the palette

```bash
ffmpeg/bin/ffmpeg.exe -i muted.mp4 -i docs/palette.png -filter_complex \
  "[0:v]scale=480:270:flags=area[small];\
   [small][1:v]paletteuse=dither=none[quant];\
   [quant]scale=1920:1080:flags=neighbor" \
  -c:v libx264 -crf 18 -pix_fmt yuv420p scene_sleeping.mp4
```

Downscale to a true pixel resolution with `area`, snap every colour to your palette
with `dither=none` — dithering would reintroduce the mush — then scale back up with
`neighbor` for hard edges. This is what kills the shimmer.

### Loop

The MOTION sections ask for a return to the first-frame arrangement, which gets you
close. Where the seam still shows:

**Ping-pong** — perfect loop, doubles duration, reverses motion. Fine where motion
is symmetrical (`sleeping`, `lunch`):

```bash
ffmpeg/bin/ffmpeg.exe -i scene.mp4 -filter_complex \
  "[0:v]split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1" \
  -c:v libx264 -crf 18 loop.mp4
```

**Crossfade tail into head** — costs a second, keeps motion one-directional.
Required for `kita_goodbye` (falling confetti) and `tubby` (rising bubbles), where
reversal reads as obviously wrong. For a 10 s source:

```bash
ffmpeg/bin/ffmpeg.exe -i scene.mp4 -filter_complex \
  "[0:v]split[a][b];[a]trim=0:9,setpts=PTS-STARTPTS[head];\
   [b]trim=9:10,setpts=PTS-STARTPTS[tail];\
   [head][tail]xfade=transition=fade:duration=1:offset=8" \
  -c:v libx264 -crf 18 loop.mp4
```

`fixing` works either way — the glitch churn is random enough that reversal doesn't
read.

### Banner fallback for `kita_goodbye`

If 2.5's lettering does drift, composite a clean still of the banner region over
every frame rather than regenerating:

```bash
ffmpeg/bin/ffmpeg.exe -i scene.mp4 -i banner.png -filter_complex \
  "[0:v][1:v]overlay=x=(W-w)/2:y=H*0.5" -c:v libx264 -crf 18 fixed.mp4
```

Or drop the banner from the generation entirely and draw it in Tk, the way
`_draw_text` already draws the scene label — cleanest, and it stays editable.

---

## 6. Button icons — image model, not Seedance

A button icon is a small transparent-background still; Seedance produces neither
stills nor alpha. These go to an image model and are model-agnostic.

### 6a. Icon style block (prepend to every button prompt)

```
Single pixel-art icon on a fully transparent background. Square canvas, 256x256,
drawn on a visible 32x32 pixel grid so each drawn pixel is a clean 8x8 block. Hard
edges only: no anti-aliasing, no soft shadow, no gradient, no bevel, no gloss
highlight, no 3D shading. Every shape carries a one-pixel dark navy #2a3142
outline. The icon is centred with two grid pixels of empty margin on all four
sides, and reads clearly when scaled down to 24x24.

Flat, friendly, toddler-facing retro-arcade look, matching a CRT television UI in
deep indigo #0d0721 with neon accents.

Negative: no text, no letters, no numbers, no drop shadow, no background plate, no
rounded-rectangle button frame, no border, no glow halo bleeding past the shape, no
photorealism, no 3D render, no smooth vector gradients, no watermark.
```

### 6b. Main app — `toddlertv.py`

| Icon | Where | Prompt body (append to icon style block) |
|------|-------|------------------------------------------|
| **Power** | `power_btn`, top bar | `A power symbol: a thick open circle with a short vertical bar breaking through the top of the ring. Produce two versions: (a) bright neon green #00ff88, the active state; (b) the identical shape in dim grey-violet #333344, the powered-off state. The two must be pixel-identical in shape.` |
| **Power hover** | `power_btn` | `The same power symbol, pixel-identical in shape, in coral red #FF6B6B — the hover state.` |
| **Cartoons** | channel 1, `#FF6B6B` | `A friendly mouse head facing forward: two large round ears, a rounded muzzle, two dot eyes, a small nose, three whiskers each side. Coral red #FF6B6B body with #ff9999 highlight on the upper left of each ear and cream #FFD93D inner ears. Cheerful, no teeth.` |
| **Songs** | channel 2, `#4ECDC4` | `A single eighth note — an oval note head with a straight stem and one flag — in teal #4ECDC4 with a pale #aaffee highlight along the stem's left edge. Beside its head, two small teal sparkle dots suggesting sound.` |
| **Learning** | channel 3, `#FFD93D` | `A five-pointed star with plump, slightly rounded points, in warm yellow #FFD93D, with a paler #fff3aa highlight wedge in the upper-left point and a small white twinkle dot just off its upper right tip.` |
| **Animals** | channel 4 default, `#6BCB77` | `An elephant head facing forward: two wide round ears, a broad forehead, two dot eyes, and a trunk curling down and to the right. Mint green #6BCB77 body with #aaffaa highlight on the upper left, and a paler inner-ear fill.` |
| **Channel placeholder** | `_draw_channel_button`, no-thumbnail state | `A blank retro television set seen front-on: a chunky bezel in muted violet #2a1a4e, a dark #0a0010 screen crossed by three faint horizontal scanlines, two small dials on the right of the bezel, and two short splayed legs. A single neon green #00ff88 status dot on the bezel's lower left.` |
| **Scan / remote** | QR overlay | `A small smartphone seen front-on, held upright: a muted violet #2a1a4e body with a one-pixel neon green #00ff88 outline and a dark screen showing a simplified three-block QR corner marker in neon green. A short neon green arc rises from its top right corner, suggesting a wireless link.` |

### 6c. Web remote — `web_remote.py`

These sit on a `#080416` page at roughly 24 px. Generate the neutral version, then
the coloured state version where the table asks for it.

| Icon | Where | Prompt body (append to icon style block) |
|------|-------|------------------------------------------|
| **Save** | `.btn-save` | `A floppy-disk save icon: a square body with a clipped top-right corner, a rectangular label panel across the lower half, and a small shutter rectangle centred at the top. Solid neon green #00ff88 with a darker #003322 outline instead of the usual dark navy — this icon sits on a green button.` |
| **Add period** | `.btn-add` | `A thick plus sign with squared ends and equal arms, in teal #4ECDC4. Nothing else in frame — simple and unmistakable at small size.` |
| **Remove from round** | `.btn-icon` ✕ | `A thick X: two equal diagonal bars crossing at the centre, squared ends, in dim grey-violet #555577. Provide a second, identical-shape version in coral red #FF6B6B for the hover state.` |
| **Delete permanently** | `.btn-icon.danger` 🗑 | `A waste bin seen front-on: a tapering body with three vertical ridge lines, a lid bar across the top slightly wider than the body, and a small handle notch centred above the lid. Dim grey-violet #555577, plus a second identical-shape version in coral red #FF6B6B for the hover state.` |
| **Sync playlist** | `.btn-sync` ⟳ | `A circular refresh arrow: a ring broken at the top right, with a solid triangular arrowhead on the clockwise end pointing along the curve. Dim grey-violet #555577, plus a second identical-shape version in neon green #00ff88 for the hover state.` |
| **Skip** | `.btn-skip` ⏭ | `A skip-forward symbol: two solid right-pointing triangles side by side, followed by a thin vertical bar. Dim grey-violet #555577, plus a second identical-shape version in teal #4ECDC4 for the hover state.` |
| **Add to channel** | `.btn-queue` | `A thick plus sign with squared equal arms, in the deep page indigo #080416 — this icon sits on a bright per-channel colour fill, so it must read as a knockout.` |
| **Play once** | `.btn-once` ▶ | `A single solid right-pointing triangle with one small dot centred just below it, suggesting a one-time play. Dim grey-violet #555577, plus a second identical-shape version in teal #4ECDC4 for the hover state.` |
| **Back to schedule** | `.back-link` ← | `A left-pointing arrow: a solid triangular head on a short thick horizontal shaft. Dim grey-violet #555577, plus a second identical-shape version in teal #4ECDC4 for the hover state.` |
| **Downloaded tag** | `.tag-local` ✓ | `A bold checkmark: a short down-right stroke meeting a longer up-right stroke, squared ends, in neon green #00ff88.` |
| **Downloading tag** | `.tag-dl` ↓ | `A downward arrow: a solid triangular head below a short thick vertical shaft, in warm yellow #FFD93D.` |
| **Streaming tag** | `.tag-stream` ~ | `A single horizontal tilde wave — one stepped pixel curve rising then falling — in teal #4ECDC4, suggesting a live stream rather than a stored file.` |
| **Play-once tag** | `.tag-once` | `A small circular clock face: a ring with two short hands, one pointing up and one pointing right, in lilac #cc88ff, suggesting a one-time temporary item.` |
| **Live indicator** | `live_label` ● LIVE | `A single filled circle in coral red #FF6B6B, with a second concentric ring around it at half brightness, suggesting a pulsing broadcast dot.` |

### 6d. Batch prompt

```
[ICON STYLE BLOCK]

Produce a single sheet of icons on a transparent background, laid out on a strict
grid of equal square cells with even gutters, every icon drawn at the same visual
weight, stroke thickness and optical size so they read as one family. Do not label
the cells. The icons, in reading order:

  1. power symbol (broken ring with a bar through the top)
  2. floppy-disk save
  3. thick plus
  4. thick X
  5. waste bin
  6. circular refresh arrow
  7. skip forward (two triangles and a bar)
  8. play triangle
  9. left arrow
 10. checkmark
 11. down arrow
 12. tilde wave
 13. clock face
 14. filled dot with concentric ring

Render the whole sheet in dim grey-violet #555577 with a one-pixel dark navy
#2a3142 outline, so the set can be recoloured per state in CSS.
```

---

## 7. Checklist before you ship a clip

- [ ] `generate_audio: false` set, AUDIO exclusion section present, `-an` applied,
      `ffprobe` confirms no audio stream. This one plays in a bedroom.
- [ ] `resolution: 1080p` — **not** any `-sr` or `-esr` variant.
- [ ] No double quotes anywhere in the prompt.
- [ ] All ten sections present. Skipping one degrades the output.
- [ ] OPTICS section intact; watch the last frame against the first for drift.
- [ ] Same seed across all five scenes; sprite references attached.
- [ ] `sleeping` generated at 30 s, not 5.
- [ ] Nothing important below 68 % height — `_draw_text` covers it.
- [ ] Bottom-right ~150×175 px clear — the QR overlay sits there.
- [ ] Top 72 px clear — the channel bar sits there.
- [ ] No buildings, road or cars anywhere.
- [ ] `kita_goodbye` banner checked frame by frame — it spells HAVE FUN AT KITA!
      correctly in every frame, or it's composited in post.
- [ ] Palette re-locked, so robot bodies still match `pixel_sprites.DEFAULT_PALETTE`
      and the live `MiniRobotCanvas` sprites on the channel buttons.
- [ ] Grid re-quantized — no shimmer, no soft edges.
- [ ] Loops without a visible seam.
- [ ] `sleeping` watched for a full minute on the actual TV before you commit. It
      runs eleven hours a day.

---

## 8. Sources

Seedance 2.5 specifics verified September 2026 against:

- [fal.ai — What is Seedance 2.5](https://fal.ai/learn/tools/what-is-seedance-2-5)
- [Atlas Cloud — Seedance 2.5 image-to-video API](https://www.atlascloud.ai/models/bytedance/seedance-2.5/image-to-video)
- [Atlas Cloud — Seedance 2.5 camera control guide](https://www.atlascloud.ai/blog/tips/seedance-2.5-camera-control-guide)
- [Higgsfield — Seedance 2.5 prompting guide](https://higgsfield.ai/blog/seedance-2-5-prompting-guide)

Parameter names differ between hosts — check yours before the first run.
