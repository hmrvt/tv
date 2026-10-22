"""Assemble ready-to-paste Seedance 2.5 prompts, one file per scene.

Shared sections live here once; per-scene sections follow. Run after editing
either and the five files in docs/prompts/ regenerate.

    .venv/Scripts/python.exe tools/build_prompts.py
"""
import os

OUT = os.path.join(os.getcwd(), "docs", "prompts")

# ── Shared sections ──────────────────────────────────────────────────────────

GLOBAL_STYLE = """GLOBAL STYLE: Chunky retro pixel-art animation, 1980s cathode-ray television aesthetic. Hard pixel blocks on a visible fixed grid, flat areas of solid colour, dithered banding instead of smooth gradients. Faint horizontal scanlines across the whole frame, gentle neon bloom around bright elements, corners falling into shadow. 16:9. Constant shutter, no motion blur, no depth of field, no film grain, no lens flare. Strict palette: deep indigo #0d0721, dark violet #1a0a3e, muted violet #2a1a4e, neon green #00ff88, teal #4ECDC4, warm yellow #FFD93D, coral red #FF6B6B. Exclusions, absolute: no buildings anywhere, no city skyline, no windows, no street, no road, no pavement, no cars, no vehicles, no lamp posts, no urban horizon. No photorealism, no 3D render, no smooth vector art, no cel-shaded anime. No logos, no watermark, no subtitles, no captions, no user-interface overlay."""

CHARACTERS = """CHARACTERS: Two boxy pixel-art robots, matching reference image 1 exactly. Body of off-white #e8ecf2 panels with grey #b8c2d0 shading and a one-pixel dark navy #2a3142 outline throughout, grey #5a6478 joints and boots. Square head with a bevelled bezel framing a dark navy #0d2540 face screen; eyes and smile glow on that screen in the robot own accent colour. Two thin antennae rising from the head with warm yellow #FFD93D tips. Chest plate carrying a small accent-coloured port and a row of three status LEDs in yellow, coral red and accent colour. Two stubby legs ending in grey boots. Friendly and rounded, toddler-safe: no teeth, no weapons, no menace. ROBOT A is the hero, left of centre, one third larger. ROBOT B is smaller, right of centre. Their proportions, outline weight and face geometry never change for the length of the clip."""

OPTICS = """OPTICS: Single continuous shot, no cuts, no transitions. Locked tripod, fixed 35mm lens, zero camera translation, zero camera rotation, zero zoom, zero dolly, zero crane, zero parallax, zero handheld micro-jitter. The frame is mechanically immobile for the entire duration: every pixel of the background sits at the same screen coordinate in the last frame as in the first. Orthographic flat-on framing, no perspective shift."""

AUDIO = """AUDIO: Silent. No music, no score, no narration, no dialogue, no voices, no sound effects, no ambient room tone, no foley."""

# ── Per-scene sections ───────────────────────────────────────────────────────

SCENES = {
    "sleeping": {
        "duration": 30,
        "refs": "docs/refs/robot_sleep.png, docs/refs/robot_idle_lavender.png",
        "scene": "SCENE: Two small robots doze under a night sky while a crescent moon glows overhead. Nothing happens. The mood is hushed, slow and safe, a lullaby.",
        "location": "LOCATION: A near-black indigo #0d0721 night sky filling the frame, dusted with small stars in white, pale blue #aaccff, warm cream #ffeeaa and mint #ccffee, each a single bright pixel with a one-pixel cross of glow. A darker indigo #0e0825 elliptical pool of shadow behind the robots. Upper right, a crescent moon in warm yellow #FFD93D, its inner curve bitten out by a disc of sky colour, haloed by three concentric rings of dim amber, with five tiny yellow sparkle-stars orbiting at a distance. Beneath the robots, a plain flat unbroken band of dark violet ground running to both frame edges, and empty negative space filling the lower third of the frame.",
        "first_frame": "FIRST FRAME AND BLOCKING: ROBOT A lies asleep on its side, left of centre, on a plump oval pillow of dark violet #2a1a4e outlined in thin neon green #00ff88, its face screen showing two flat horizontal dashes for closed eyes and a small soft curve of a mouth in teal, antenna tips dimmed to low amber. ROBOT B sits upright right of centre in lavender accent, drowsy but awake, one eye narrower than the other, head tilted toward its sleeping friend. Both heads sit at 42 percent of the frame height. Neither robot changes position on screen for the whole clip.",
        "motion": "MOTION: Continuous, cyclic, uneventful. Stars twinkle softly out of phase with one another at roughly one cycle per second each. Both robots breathe with a slow three-pixel vertical rise and fall in opposite phase, one settling as the other lifts, about twelve full cycles across the clip. Neon green lowercase z glyphs drift up and to the right from ROBOT A head, one at a time, swaying gently sideways and fading out near the top of frame, a new one beginning as the previous one vanishes. The warm yellow sparkles beside ROBOT B orbit slowly around its head. The amber rings around the moon pulse almost imperceptibly. At 28s the scene returns to exactly the arrangement of the first frame, so the clip can loop.",
        "physics": "PHYSICS: Motion is gentle and weightless. The z glyphs rise at constant slow speed with a slight sinusoidal sideways sway, never accelerating. The pillow does not compress or deform. Nothing falls, nothing collides, nothing is disturbed by wind.",
        "lighting": "LIGHTING: Lit only by the moon and by the robots own face screens and status LEDs, so the off-white bodies read cool grey-violet rather than white. Very low contrast throughout, deep shadow in the corners, no light source bright enough to wake a sleeping child. Lighting is constant, with no flicker and no shift in colour temperature.",
    },
    "lunch": {
        "duration": 10,
        "refs": "docs/refs/robot_eat.png, docs/refs/robot_idle_teal.png",
        "scene": "SCENE: Two robots share a quiet meal at a low table under a bright midday sky. Warm, domestic, unhurried.",
        "location": "LOCATION: A three-band dithered sky, deeper blue #5ba3d9 across the top third stepping down to light sky blue #87CEEB below, with visible pixel dithering at the band edges rather than a smooth blend. A warm yellow #FFD93D sun disc at upper right inside three rings of dim amber. Three pale blue-white #deeeff cloud puffs high in frame, each one large circle flanked by two smaller ones, flat-filled with a single highlight step. Centre frame, a low table in dark violet #2a1a4e outlined in thin neon green #00ff88, with a pale highlight line along its front edge and two square legs. On it, a dark oval plate of #1a0a3e rimmed in neon green holding three blocky food shapes, one coral red #FF6B6B, one cream #FFD93D, one teal #4ECDC4, each a plain rectangle of flat colour. To the right, a square-sided violet mug outlined in neon green with a single-pixel C-shaped handle, filled with teal liquid. Beneath, a plain flat unbroken band of dark violet ground running to both frame edges, empty negative space in the lower third.",
        "first_frame": "FIRST FRAME AND BLOCKING: ROBOT A sits at the table left of centre, leaning slightly forward mid-meal, mint accent, face screen showing two upturned chevron eyes and a wide smile, right arm raised holding a small neon green three-tined fork. ROBOT B stands right of centre in orange accent, arms down, content neutral face, watching its friend eat. Both heads at 42 percent of the frame height. Neither robot changes position on screen for the whole clip.",
        "motion": "MOTION: Continuous and cyclic. Steam rises from the plate and from the mug in slow curling wisps that thin and fade near the top of their travel, new wisps continuously replacing them. The three cloud puffs drift very slowly leftward at constant speed. Both robots bob three pixels vertically in opposite phase. ROBOT A chews, its mouth shape alternating between a small open square and a closed curve about twice per second, while its forked hand lifts a few pixels and lowers again in an unhurried eating rhythm. ROBOT B head turns very slightly toward its friend and back. The amber rings around the sun pulse faintly. At 9s the scene returns to the arrangement of the first frame, so the clip can loop.",
        "physics": "PHYSICS: Steam rises at constant slow speed with gentle sideways drift, expanding slightly as it thins. Clouds translate horizontally without deforming. The table, plate and mug are rigid and never wobble, tip or slide. Nothing spills.",
        "lighting": "LIGHTING: Flat high-key daylight from the upper right, shadowless and cheerful, so the robot bodies read bright white-cream with grey #b8c2d0 shading only on their left sides. Lighting is constant, no flicker, no shift in colour temperature.",
    },
    "fixing": {
        "duration": 10,
        "refs": "docs/refs/robot_fix.png, docs/refs/robot_wave.png",
        "scene": "SCENE: Two robot engineers repair a broken television set under a flat grey sky. Busy, industrious, faintly comic.",
        "location": "LOCATION: A dithered two-step overcast sky from slate grey-blue #546e7a across the top to lighter grey #90a4ae below, no blue and no warmth, deliberately drab. A pale washed-out disc #b0bec5 of sun barely showing at upper right, its glow rings almost invisible. Three dull pale cloud puffs high in frame. Centre frame, a broken retro television set facing the viewer: a chunky outer bezel in dark plum #221133 with a bold two-pixel coral red #FF6B6B border, enclosing a near-black #0a0010 screen crossed by evenly spaced dark horizontal scanlines in #110022. Ten small horizontal glitch bars of harsh neon green #00ff88 scattered across that screen at irregular positions. A coral red warning triangle at the screen lower edge. Below the set, a stubby two-tier stand in dark violet #2a1a4e. Beneath, a plain flat unbroken band of dark violet ground running to both frame edges, empty negative space in the lower third.",
        "first_frame": "FIRST FRAME AND BLOCKING: ROBOT A stands left of centre leaning in toward the television at work, sky blue accent, face screen showing two round dot eyes and a short diagonal slash of a mouth, right arm extended toward the set holding a neon green wrench with a small circular ring head on a thick angled shaft. ROBOT B stands right of centre in pink accent supervising, one hand raised in a small wave, the other holding a clipboard: a dark violet rectangle outlined in neon green with a darker indigo clip bar across the top and three horizontal neon green ruled lines below. Both heads at 42 percent of the frame height. Neither robot changes position on screen for the whole clip.",
        "motion": "MOTION: Continuous and cyclic. The neon green glitch bars inside the television screen scramble to new positions and lengths several times a second, a fast nervous static churn confined strictly inside the screen rectangle. The coral red warning triangle pulses about twice a second. Sparks burst from the set upper-left corner in repeating volleys of five four-pointed glyphs, each arcing up and outward before vanishing. ROBOT A wrench arm makes a small repeating tightening motion against the bezel. ROBOT B raised hand waves slowly. Both robots bob two pixels vertically in opposite phase. At 9s the scene returns to the arrangement of the first frame, so the clip can loop.",
        "physics": "PHYSICS: Sparks launch on ballistic arcs and fade at the top of their travel, never bouncing and never landing. The television set, its stand, the wrench and the clipboard are rigid: they never wobble, rotate, tip, stretch or scale. The glitch churn is a flat screen effect with no depth and casts no light on the robots.",
        "lighting": "LIGHTING: Cold, diffuse, shadowless overcast light. The only warm light in frame comes from the sparks and from the robots own status LEDs, and it does not spill onto the ground. Base lighting is constant, with no flicker except the sparks themselves.",
    },
    "kita_goodbye": {
        "duration": 10,
        "refs": "docs/refs/robot_cheer.png, docs/refs/robot_wave.png",
        "scene": "SCENE: Two robots celebrate a cheerful goodbye beneath a huge sunrise, with bunting and falling confetti. Joyful, bouncy, warm.",
        "location": "LOCATION: A dithered sky from burnt orange #e07a3a across the top to soft golden sand #f4c47a below, visibly banded. Three pale cloud puffs catching the light high in frame. Dominating the centre, a large sunrise disc in bright orange #FF8C42 ringed with a three-pixel warm yellow #FFD93D outline and haloed by three concentric rings of deep ember red, with twelve straight yellow rays radiating at even thirty-degree intervals, each a different length. Floating above the sun, a small warm-coloured pixel school backpack with two straps and a front pocket. Six celebratory pixel motifs orbit the sun in a wide ellipse, evenly spaced: a yellow four-point star, a yellow heart, a larger sparkle-star, a white twinkle, a mint green #6BCB77 heart, and a coral red balloon on a single-pixel string. Across the middle of the frame at 55 percent height, a horizontal celebration banner: a deep indigo #1a004e rectangle with a hard two-pixel warm yellow #FFD93D border and a solid dark drop shadow offset three pixels down and right, bearing the words HAVE FUN AT KITA! in blocky warm-yellow monospace pixel capitals, centred, spelled exactly that way and held perfectly stable and legible for the entire clip. Above the banner, a row of small downward-pointing triangular pennants alternating coral red #FF6B6B, teal #4ECDC4 and warm yellow. Beneath the robots, a plain flat unbroken band of ground running to both frame edges, empty negative space in the lower third.",
        "first_frame": "FIRST FRAME AND BLOCKING: ROBOT A stands left of centre cheering with both arms thrown high above its head, yellow accent, face screen showing two upturned chevron eyes and a wide open grin. ROBOT B stands right of centre in teal accent waving farewell with one arm, carrying a small lunchbox in the other: a dark violet rectangle outlined in teal with a simple divided bento pattern on its face. Both heads at 42 percent of the frame height. Neither robot changes position on screen for the whole clip.",
        "motion": "MOTION: Continuous and cyclic. The sun twelve rays rotate slowly clockwise while individually pulsing in length. The six celebratory motifs travel steadily around their elliptical orbit, completing exactly one circuit across the clip. The school backpack hovers with a slow ten-pixel rise and fall. The pennant row ripples in a left-to-right travelling wave. Confetti falls continuously across the whole frame, each piece tumbling and alternating between a small circle and a short horizontal bar, entering at the top as others leave the bottom. ROBOT A bounces on the spot with both arms raised. ROBOT B arm waves in farewell, its lunchbox swinging very slightly. The banner and its lettering do not move, warp, ripple or change. At 9s the scene returns to the arrangement of the first frame, so the clip can loop.",
        "physics": "PHYSICS: Confetti falls at varied constant speeds with gentle lateral tumble, never accelerating hard and never piling up on the ground. The pennant wave is a shallow fabric ripple that does not detach the pennants from their line. The banner is rigid board, not cloth: it does not sag, flap or curl. The sun does not drift or scale.",
        "lighting": "LIGHTING: Warm golden directional light from the central sun, throwing warm highlights along the robots inner edges and cool violet shadow on their outer edges. Constant, no flicker, no shift in colour temperature.",
    },
    "tubby": {
        "duration": 10,
        "refs": "docs/refs/robot_cheer.png, docs/refs/robot_dance.png",
        "scene": "SCENE: Two robots enjoy a bubble bath in a clawfoot tub under a purple evening sky. Warm, playful, splashy.",
        "location": "LOCATION: A dithered sky from rich purple #6b3fa0 across the top to soft lilac #c9a8e0 below, with a muted apricot #f4a261 sun low at upper right inside three faint glow rings and three pale cloud puffs drifting high. Dominating the frame, a wide freestanding clawfoot bathtub seen front-on spanning most of the frame width: body of deep steel blue #1a3850 with a two-pixel teal #4ECDC4 outline, a rolled rim in slate #253e52 across the top as a flattened ellipse with a pale #3a607e shine stripe along its upper left, and water inside in dark teal-navy #0a3a55. Four brass clawfoot legs in warm gold #c8a030 with darker #8b6014 outlines, each a short shaft, a rounded ball and three small gripping toes. At the right end of the rim a tall brass faucet, a square spout column with a rounded outlet beneath. At the left end, two round brass valve knobs, one marked with a coral red #FF6B6B bar and one with a teal bar. A thick row of pale blue-white #c8e8f8 foam suds along the waterline as overlapping flattened circles of varied size. Floating centre-right, a large bright yellow pixel rubber duck with an orange beak and a single dark eye dot. Empty negative space fills the lower third of the frame.",
        "first_frame": "FIRST FRAME AND BLOCKING: Both robots sit half-submerged in the tub, visible from the chest up with the foam line cutting across them. ROBOT A is left of centre in pink accent with both arms raised in delight, face screen showing two upturned chevron eyes and a wide open grin. ROBOT B is right of centre in lavender accent, dancing with arms out to either side, star-shaped sparkle eyes and an open grin. Both heads at 42 percent of the frame height. Neither robot changes position on screen for the whole clip.",
        "motion": "MOTION: Continuous and cyclic. The foam suds undulate along the waterline in a slow left-to-right travelling wave. Soap bubbles rise continuously from the water surface as thin lilac #c9a8e0 outlined circles each with a small white highlight arc at upper left, drifting up past the rim and off the top of frame, new ones spawning at the waterline as older ones leave. Four soft #c8e8f8 steam wisps drift upward above the rim and dissipate. The rubber duck bobs gently with a five-pixel rise and fall. Teal droplets fall from the brass faucet in a repeating chain into the water. Around ROBOT A, splash droplets burst outward in expanding rings. Both robots bob four pixels vertically in opposite phase, ROBOT B alternating its arms up and down. At 9s the scene returns to the arrangement of the first frame, so the clip can loop.",
        "physics": "PHYSICS: The bathtub is completely rigid and motionless: its body, rim, four brass clawfoot legs, faucet and both valve knobs stay at fixed screen coordinates and never wobble, tilt, stretch, scale or sway. The water surface moves only as the shallow foam wave; the water level never rises or falls. Bubbles rise at constant slow speed without popping. Droplets fall in straight lines under gentle gravity and merge into the water without splashing the tub exterior. No water leaves the tub.",
        "lighting": "LIGHTING: Soft warm-cool evening light from the upper right, with cool teal bounce light thrown up onto the robots chins and chests from the water. Constant, no flicker, no shift in colour temperature.",
    },
}

ORDER = ["sleeping", "lunch", "fixing", "kita_goodbye", "tubby"]


def build(name, s):
    body = "\n\n".join([
        GLOBAL_STYLE, s["scene"], CHARACTERS, s["location"],
        s["first_frame"], s["motion"], OPTICS, s["physics"],
        s["lighting"], AUDIO,
    ])
    header = (
        f"# {name} — paste everything below the dashed line into Seedance 2.5\n"
        f"#\n"
        f"# Settings:  duration={s['duration']}  resolution=1080p  "
        f"generate_audio=false\n"
        f"# image_url: your {name} first frame (see docs/seedance_prompts.md section 3)\n"
        f"# references: {s['refs']}\n"
        f"# Reuse one seed across all five scenes.\n"
        f"# ---------------------------------------------------------------\n\n"
    )
    return header + body + "\n"


def main():
    os.makedirs(OUT, exist_ok=True)
    for i, name in enumerate(ORDER, 1):
        path = os.path.join(OUT, f"{i:02d}_{name}.txt")
        text = build(name, SCENES[name])
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        words = len(text.split())
        print(f"{os.path.basename(path):<24} {words:>5} words")
    print(f"\n{len(ORDER)} files in {OUT}")


if __name__ == "__main__":
    main()
