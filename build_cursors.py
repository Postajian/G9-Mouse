"""
G9 custom Windows cursors - generator for two variants, each with a pressed state.

VARIANT "reticle"   (from the user's reference photo)
    resting  4 GINGER corner brackets + solid BLUE square on the hotspot
    pressed  the two colours reverse: blue brackets, ginger centre

VARIANT "octagram"  (from S Y M B O L\\88.svg + the Wikipedia variants sheet)
    The 8-point star, Rub el Hizb. Referred to here as the 8-star style.
    resting  OUTLINE    - two squares at 0 and 45 degrees, ONE stroke each, so
                          two lines on screen in total. Light, airy.
    pressed  INTERLACED - the same two squares as bold solid bands that weave
                          over and under at all 8 crossings. Colours hold; the
                          line weight alone carries the state change.

All 15 Windows cursor roles are generated for every state. The frame is the
family signature and only the centre glyph changes per role, so the whole set
reads as one system. Hotspot is the centre of the shape for every size.

Shapes are plain polygons rasterised at 4x supersample and Lanczos-downsampled,
so there is no SVG rasteriser, Inkscape or ImageMagick dependency. Output is
multi-resolution .cur (32/48/64/96/128) plus .ani for the two animated roles.
"""

import math
import os
import struct

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- constants

DESIGN = 128.0
SS = 4
SIZES = [32, 48, 64, 96, 128]
ANI_SIZES = [32, 48, 64]

CYAN = (0, 217, 255, 255)
GINGER = (255, 122, 24, 255)
EDGE = (8, 19, 31, 255)     # dark rim, so both colours stay legible on white

# Inner marks are WHITE with a thin BLACK outline. Tried black-with-a-white-halo
# first and it lost: a black mark is only as visible as its halo, so the halo had
# to be thick enough to become the shape. White fill carries itself on a dark
# window, and the thin black line is all that is needed on a light one.
INK = (255, 255, 255, 255)
INK_EDGE = (0, 0, 0, 255)
EDGE_W = 3.0
INK_EDGE_W = 3.5     # thin, but not hairline: at 2.5 the white marks vanished
                     # against a white window, since the outline was the only
                     # thing defining them there. 0.9 px at a 32 px cursor.

C = 64.0                    # centre of the design space

# How much of the cursor box the artwork fills. Windows refuses to make the
# cursor surface itself smaller than 32 px (measured: CursorBaseSize below 32 is
# clamped), so the only honest way to give a SMALLER-LOOKING pointer is to draw
# a smaller mark inside the same box. 1.0 fills the box as designed.
SCALE = 1.0

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cursors")
TIMES = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "times.ttf")

# Roles the user deleted on purpose, as {variant: [role, ...]}. Without this a
# rebuild would silently recreate them - and the panel rebuilds on every colour
# or size change, so a deletion would never survive the next click.
REMOVED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "removed.json")


def removed_roles(variant):
    try:
        import json
        with open(REMOVED_FILE, encoding="utf-8") as fh:
            return set(json.load(fh).get(variant, []))
    except (OSError, ValueError):
        return set()


# ------------------------------------------------------------- geometry util


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def signed_area(p):
    return 0.5 * sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1]
                     for i in range(len(p)))


def ccw(p):
    return p if signed_area(p) > 0 else p[::-1]


def rotate(poly, deg, cx=C, cy=C):
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [((x - cx) * ca - (y - cy) * sa + cx,
             (x - cx) * sa + (y - cy) * ca + cy) for x, y in poly]


def clip(subject, clipper):
    """Sutherland-Hodgman intersection of two convex polygons."""
    out = ccw(subject)
    cl = ccw(clipper)
    for i in range(len(cl)):
        ax, ay = cl[i]
        bx, by = cl[(i + 1) % len(cl)]
        inp, out = out, []
        if not inp:
            return []
        for j in range(len(inp)):
            px, py = inp[j]
            qx, qy = inp[(j + 1) % len(inp)]
            sp = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
            sq = (bx - ax) * (qy - ay) - (by - ay) * (qx - ax)
            if sp >= 0:
                out.append((px, py))
            if (sp > 0) != (sq > 0):
                t = sp / (sp - sq)
                out.append((px + t * (qx - px), py + t * (qy - py)))
    return out


def brackets(x0, y0, x1, y1, arm, w):
    """The 4 corner L-brackets of a square, as 8 rectangles."""
    return [
        rect(x0, y0, x0 + arm, y0 + w), rect(x0, y0, x0 + w, y0 + arm),      # TL
        rect(x1 - arm, y0, x1, y0 + w), rect(x1 - w, y0, x1, y0 + arm),      # TR
        rect(x1 - arm, y1 - w, x1, y1), rect(x1 - w, y1 - arm, x1, y1),      # BR
        rect(x0, y1 - w, x0 + arm, y1), rect(x0, y1 - arm, x0 + w, y1),      # BL
    ]


def ring(r, w):
    """Hollow square centred on C: path at radius r, stroke width w."""
    h = w / 2.0
    x0, y0, x1, y1 = C - r, C - r, C + r, C + r
    return [rect(x0 - h, y0 - h, x1 + h, y0 + h),
            rect(x0 - h, y1 - h, x1 + h, y1 + h),
            rect(x0 - h, y0 + h, x0 + h, y1 - h),
            rect(x1 - h, y0 + h, x1 + h, y1 - h)]


# -------------------------------------------------------------- the two frames

# reticle
CENTRE = rect(46, 46, 82, 82)

# How far the four brackets sit from the centre, as a fraction pulled IN from
# the full box. Two numbers, both adjustable from the G9 PC Control panel so
# they can be dialled by eye instead of guessed here:
#   REST_CLOSE   where they sit normally
#   PRESS_CLOSE  where they snap to while a button is held
REST_CLOSE = 0.17
PRESS_CLOSE = 0.27

# The uncontracted frames these are derived from.
_OUTER_BOX = (6, 6, 122, 122, 38, 8)
_TIGHT_BOX = (14, 14, 114, 114, 32, 8)


def brackets_closed(x0, y0, x1, y1, arm, w, fraction):
    """Brackets pulled toward the centre, KEEPING the stroke weight.

    Scaling the whole polygon would have thinned the arms by the same fraction
    and read as the reticle receding rather than closing. Only the square and
    the arm length move; w stays put, so the frame stays as solid as it was.
    """
    k = 1.0 - fraction
    return brackets(C + (x0 - C) * k, C + (y0 - C) * k,
                    C + (x1 - C) * k, C + (y1 - C) * k, arm * k, w)


def set_spacing(rest=None, press=None):
    """Recompute the reticle frames for new spacing. Called before a build."""
    global REST_CLOSE, PRESS_CLOSE
    global OUTER, OUTER_TIGHT, OUTER_PRESSED, OUTER_TIGHT_PRESSED
    if rest is not None:
        REST_CLOSE = max(0.0, min(0.6, float(rest)))
    if press is not None:
        PRESS_CLOSE = max(0.0, min(0.6, float(press)))
    OUTER = brackets_closed(*_OUTER_BOX, fraction=REST_CLOSE)
    OUTER_TIGHT = brackets_closed(*_TIGHT_BOX, fraction=REST_CLOSE)
    OUTER_PRESSED = brackets_closed(*_OUTER_BOX, fraction=PRESS_CLOSE)
    OUTER_TIGHT_PRESSED = brackets_closed(*_TIGHT_BOX, fraction=PRESS_CLOSE)


set_spacing()

# octagram. R keeps the 45-degree square's corners inside the 128 box:
# (R + BAND_H) * sqrt(2) + rim must stay under 64.
R = 36.0
BAND_H = 6.5            # half the width of a solid band. Bumped from 5.0 once
                        # the resting outline dropped to a single stroke: the
                        # two states were only 1.4 vs 2.5 px apart at 32 px,
                        # which is not enough to read as a state change.
LINE_W = 5.5            # the single stroke of the resting outline

BAND = ring(R, BAND_H * 2)      # solid band, interlaced state

# ONE line per square, not two. The Wikipedia "outline" variant traces both
# edges of each band, which put four lines on screen for two squares and read as
# clutter at cursor size. A single stroke per square is the shape from 88.svg.
OUTLINE = ring(R, LINE_W)

# Hand used to be told apart by a filled centre. With the centre now empty it
# needs another signal, so its star closes inward the way the reticle's
# brackets do.
R_TIGHT = R - 7
BAND_TIGHT = ring(R_TIGHT, BAND_H * 2)
OUTLINE_TIGHT = ring(R_TIGHT, LINE_W)


def bracket_pairs(bars, swapped):
    """Colour the 4 corner brackets as two opposite-diagonal pairs.

    brackets() returns TL, TR, BR, BL as two rectangles each, so the diagonals
    are indices 2,3,6,7 (top-right + bottom-left) and 0,1,4,5 (top-left +
    bottom-right). Colouring by diagonal is also what makes the spinner
    checker: rotating the frame 90 degrees carries each pair onto the other
    pair's screen position, so the colours alternate on their own as it turns.
    """
    diag_a = {2, 3, 6, 7}
    first, second = ("cyan", "ginger") if swapped else ("ginger", "cyan")
    return [(p, first if i in diag_a else second) for i, p in enumerate(bars)]


def weave(bars_a, bars_b):
    """Crossing quads where square A must be redrawn on top of square B.

    The 8 crossings are sorted by angle around the centre and alternated, which
    is what makes the star read as woven rather than as two stacked squares.
    """
    xs = []
    for a in bars_a:
        for b in bars_b:
            q = clip(a, b)
            if len(q) >= 3 and abs(signed_area(q)) > 1.0:
                cx = sum(p[0] for p in q) / len(q)
                cy = sum(p[1] for p in q) / len(q)
                xs.append((math.atan2(cy - C, cx - C), q))
    xs.sort(key=lambda t: t[0])
    return [q for i, (_, q) in enumerate(xs) if i % 2 == 1]


# ---------------------------------------------------------------- role glyphs


def _dbl_arrow_ns():
    return [rect(60, 46, 68, 82),
            [(64, 34), (52, 50), (76, 50)],
            [(64, 94), (52, 78), (76, 78)]]


def _size_all():
    return [rect(60, 50, 68, 78), rect(50, 60, 78, 68),
            [(64, 36), (53, 52), (75, 52)],
            [(64, 92), (53, 76), (75, 76)],
            [(36, 64), (52, 53), (52, 75)],
            [(92, 64), (76, 53), (76, 75)]]


# Every centre mark is normalised to this span, so the set reads as one family
# instead of each glyph being sized by eye. Measured spans before this existed
# ranged from 46 (NWPen) to 60 (the size arrows) - a 30% spread.
GLYPH_SPAN = 58.0


def normalise_glyph(polys):
    """Scale a glyph about the centre so its longest side is GLYPH_SPAN."""
    if not polys:
        return polys
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    if span <= 0:
        return polys
    k = GLYPH_SPAN / span
    return [[(C + (x - C) * k, C + (y - C) * k) for x, y in p] for p in polys]


def _glyph_for(role):
    """The raw mark for a role, before it is normalised to one size."""
    if role in ("Arrow", "Hand", "Help", "Wait", "AppStarting"):
        return []
    if role == "IBeam":
        return [rect(60, 40, 68, 88), rect(52, 40, 76, 47), rect(52, 81, 76, 88)]
    if role == "Crosshair":
        return [rect(60, 36, 68, 92), rect(36, 60, 92, 68)]
    if role == "No":
        # A cross, not a single slash. One diagonal read as a stray mark rather
        # than a symbol, and at cursor size it was easy to miss entirely.
        return [[(40, 48), (48, 40), (88, 80), (80, 88)],
                [(80, 40), (88, 48), (48, 88), (40, 80)]]
    if role == "UpArrow":
        return [rect(59, 62, 69, 92), [(64, 36), (46, 66), (82, 66)]]
    if role == "SizeNS":
        return _dbl_arrow_ns()
    if role == "SizeWE":
        return [rotate(p, 90) for p in _dbl_arrow_ns()]
    if role == "SizeNWSE":
        return [rotate(p, 45) for p in _dbl_arrow_ns()]
    if role == "SizeNESW":
        return [rotate(p, -45) for p in _dbl_arrow_ns()]
    if role == "SizeAll":
        return _size_all()
    if role == "NWPen":
        return [[(42, 86), (49, 61), (67, 79)],
                [(55, 55), (73, 73), (86, 58), (68, 40)]]
    raise ValueError("unknown role " + role)


def centre_glyph(role):
    """What sits on the hotspot, normalised so every mark is the same size.

    The centre stays EMPTY unless a role genuinely needs a mark there. A filled
    square on the hotspot covered the very pixel being pointed at, so those
    roles show an open middle and are told apart by their frame instead: Hand
    closes the frame in, Wait and AppStarting spin it.

    Normalising at this one exit rather than sizing each mark by hand is what
    keeps them a family - hand-placed coordinates had drifted to a 30% spread.
    """
    return normalise_glyph(_glyph_for(role))


ROLES_STATIC = ["Arrow", "Hand", "IBeam", "Crosshair", "No", "Help", "UpArrow",
                "SizeNS", "SizeWE", "SizeNWSE", "SizeNESW", "SizeAll", "NWPen"]
ROLES_ANI = [("Wait", 12, 5), ("AppStarting", 12, 8)]


def role_shapes(variant, role, pressed, angle=0.0):
    """-> (shapes, overdraw). Both are [(polygon, 'cyan'|'ginger'), ...].

    Colours are written out literally per state rather than being flipped at
    render time, because the two variants signal 'pressed' differently:
      reticle  - the two colours reverse
      octagram - the colours hold and the line weight changes instead
    """
    if variant == "reticle":
        if role == "Hand":
            bars = OUTER_TIGHT_PRESSED if pressed else OUTER_TIGHT
        else:
            bars = OUTER_PRESSED if pressed else OUTER
        bars = [rotate(p, angle) for p in bars] if angle else bars
        # Two ginger and two cyan, on opposite diagonals, and pressing swaps
        # them. The inner mark is white-on-black in both states, matching the
        # 8 Star, so the diagonal pair is the only thing carrying the state.
        shapes = bracket_pairs(bars, pressed)
        shapes += [(p, "ink") for p in centre_glyph(role)]
        return shapes, []

    # octagram: thin outline while resting, bold interlaced bands while pressed
    tight = role == "Hand"
    base = (BAND_TIGHT if tight else BAND) if pressed else \
           (OUTLINE_TIGHT if tight else OUTLINE)
    solid = BAND_TIGHT if tight else BAND
    b_bars = [rotate(p, 45 + angle) for p in base]

    # The 8 Star's inner marks are black, not ginger: the two squares already
    # carry the colour, and a third coloured thing in the middle competed with
    # them. Black reads as a separate layer instead of another ring.
    shapes = [(p, "cyan") for p in base] + [(p, "ginger") for p in b_bars]
    shapes += [(p, "ink") for p in centre_glyph(role)]

    # The weave is only computed for the bold bands. At outline weight the
    # over/under breaks land below one pixel even at 128, so they are skipped.
    over = []
    if pressed:
        solid_b = [rotate(p, 45 + angle) for p in solid]
        over = [(q, "cyan") for q in weave(solid, solid_b)]
    return shapes, over


# -------------------------------------------------------------------- render


def set_scale(scale):
    global SCALE
    SCALE = max(0.25, min(1.0, float(scale)))


def shrink(poly):
    """Pull a polygon toward the centre by SCALE. Shape is untouched at 1.0."""
    if SCALE == 1.0:
        return poly
    return [(C + (x - C) * SCALE, C + (y - C) * SCALE) for x, y in poly]


def render(shapes, over, size, swap, help_glyph=False):
    n = size * SS
    k = n / DESIGN
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    cyan, ginger = (GINGER, CYAN) if swap else (CYAN, GINGER)
    fill = {"cyan": cyan, "ginger": ginger, "ink": INK}
    rims = {"cyan": EDGE, "ginger": EDGE, "ink": INK_EDGE}

    # Rim pass. Drawn as a real centred stroke rather than by offsetting the
    # polygon: pushing vertices out from the centroid overshoots the ends of a
    # long thin bar and leaves dark nubs at every corner of the star.
    # rim scales with the mark, so a shrunken pointer does not end up mostly rim
    rim = max(1, int(round(EDGE_W * k * SCALE * 2)))
    ink_rim = max(1, int(round(INK_EDGE_W * k * SCALE * 2)))
    for poly, name in shapes:
        pts = [(x * k, y * k) for x, y in shrink(poly)]
        w = ink_rim if name == "ink" else rim
        d.polygon(pts, fill=rims[name], outline=rims[name], width=w)
    for poly, name in shapes:
        d.polygon([(x * k, y * k) for x, y in shrink(poly)], fill=fill[name])
    for poly, name in over:
        d.polygon([(x * k, y * k) for x, y in shrink(poly)], fill=fill[name])

    if help_glyph:
        ink = help_glyph == "ink"
        glyph_fill, glyph_rim = (INK, INK_EDGE) if ink else (ginger, EDGE)
        try:
            # Coloured with a dark rim, not dark on a filled square. The square
            # behind it is gone, so a dark glyph alone would vanish on a dark
            # window.
            # Sized to match the arrow glyphs, which span about 60 of the 128 design
            # units. Times renders a "?" at roughly two thirds of its point size,
            # so 88 lands the glyph in the same visual weight class.
            # 84 pt, not a round number: measured, the Times "?" renders 55.2 units
            # tall at 80 and 61.0 at 88, so 84 lands on the GLYPH_SPAN of 58 that
            # every polygon mark is normalised to.
            f = ImageFont.truetype(TIMES, max(6, int(84 * k * SCALE)))
            layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
            ImageDraw.Draw(layer).text(
                (C * k, (C + 1) * k), "?", font=f, anchor="mm", fill=glyph_fill,
                stroke_width=max(1, int((INK_EDGE_W if ink else EDGE_W) * k * SCALE)),
                stroke_fill=glyph_rim)
            img.alpha_composite(layer)
        except OSError:
            print("  ! times.ttf missing - Help has no glyph")

    return img.resize((size, size), Image.LANCZOS)


# ------------------------------------------------------------ .cur / .ani I/O


def dib(img):
    """32bpp bottom-up BITMAPINFOHEADER DIB + zeroed AND mask."""
    w, h = img.size
    px = img.load()
    head = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    xor = bytearray()
    for y in range(h - 1, -1, -1):
        for x in range(w):
            r, g, b, a = px[x, y]
            xor += bytes((b, g, r, a))
    mask_row = (w + 31) // 32 * 4
    return head + bytes(xor) + b"\x00" * (mask_row * h)


def cur_bytes(images):
    """Pack {size: Image} into one .cur with a centred hotspot per size."""
    entries, blobs = [], []
    offset = 6 + 16 * len(images)
    for size in sorted(images):
        data = dib(images[size])
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0,
                                   size // 2, size // 2, len(data), offset))
        blobs.append(data)
        offset += len(data)
    return struct.pack("<HHH", 0, 2, len(images)) + b"".join(entries) + b"".join(blobs)


def riff(tag, payload):
    out = tag + struct.pack("<I", len(payload)) + payload
    return out + (b"\x00" if len(payload) % 2 else b"")


def ani_bytes(frames, jif_rate):
    anih = struct.pack("<9I", 36, len(frames), len(frames), 0, 0, 0, 0, jif_rate, 1)
    body = b"ACON" + riff(b"anih", anih)
    body += riff(b"LIST", b"fram" + b"".join(riff(b"icon", f) for f in frames))
    return riff(b"RIFF", body)


# ---------------------------------------------------------------------- main


def build(variant, pressed, sub):
    out = os.path.join(OUT_DIR, variant, sub)
    os.makedirs(out, exist_ok=True)
    made = []
    skip = removed_roles(variant)

    for role in ROLES_STATIC:
        if role in skip:
            continue
        shapes, over = role_shapes(variant, role, pressed)
        hg = "ink" if role == "Help" else False
        imgs = {s: render(shapes, over, s, False, help_glyph=hg)
                for s in SIZES}
        p = os.path.join(out, role + ".cur")
        open(p, "wb").write(cur_bytes(imgs))
        made.append(p)

    for role, steps, jif in ROLES_ANI:
        if role in skip:
            continue
        frames = []
        for i in range(steps):
            # both frames are 4-fold symmetric, so 90 degrees is a full cycle
            shapes, over = role_shapes(variant, role, pressed, i * 90.0 / steps)
            imgs = {s: render(shapes, over, s, False) for s in ANI_SIZES}
            frames.append(cur_bytes(imgs))
        p = os.path.join(out, role + ".ani")
        open(p, "wb").write(ani_bytes(frames, jif))
        made.append(p)

    return made


def parse_colour(text):
    """#RRGGBB or RRGGBB -> RGBA tuple."""
    t = text.strip().lstrip("#")
    if len(t) != 6:
        raise ValueError("colour must be 6 hex digits, got %r" % text)
    return tuple(int(t[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


def set_colours(a=None, b=None):
    """a is the 'cyan' slot, b is the 'ginger' slot. Render reads these globals."""
    global CYAN, GINGER
    if a:
        CYAN = parse_colour(a)
    if b:
        GINGER = parse_colour(b)


# Hold BOTH mouse buttons and the pointer spins, stepping up a gear every couple
# of seconds.
#
# Every gear plays at the SAME frame rate and differs only in how far it turns
# per frame. The first attempt did the opposite - one frame count, faster
# playback per gear - and the slow gears stuttered badly: 12 frames at gear 1
# is 7.5 frames per second, which the eye reads as jumping, not turning.
# Smoothness is frames per second; speed is degrees per frame. Separating them
# lets gear 1 be slow AND smooth.
SPIN_JIF = 2                            # 30 fps, every gear
SPIN_FRAMES = [48, 36, 24, 12, 6]       # 3.75 / 5 / 7.5 / 15 / 30 deg per frame
SPIN_SPAN = 180.0                       # degrees one loop covers

# Kept as a derived list so anything asking "how many gears" still works.
SPIN_RATES = [SPIN_JIF] * len(SPIN_FRAMES)


def build_spin(variant):
    """The speed gears of the both-buttons spinner, for one variant.

    Both variants turn at the SAME angular speed. They used to differ: the loop
    span was set from each shape's symmetry - 90 degrees for the 4-fold
    octagram, 180 for the reticle whose diagonal colours make it only 2-fold.
    Equal frames over half the arc meant the 8 Star really did run at half
    speed. 180 is a whole multiple of both symmetries, so both loop seamlessly.
    """
    out = os.path.join(OUT_DIR, variant, "spin")
    os.makedirs(out, exist_ok=True)
    made = []

    # A span that is not a whole multiple of the shape's symmetry makes the
    # animation jump when it loops. Asserted rather than commented, because the
    # jump is subtle at speed and easy to ship without noticing.
    symmetry = {"reticle": 180.0, "octagram": 90.0}.get(variant, 360.0)
    assert SPIN_SPAN % symmetry == 0, (
        "spin span %g does not loop cleanly for %s (symmetry %g)"
        % (SPIN_SPAN, variant, symmetry))

    # Each gear is rendered at its own frame count, so the slow gears get fine
    # angular steps and the fast ones coarse. Playback rate is constant, which
    # is what actually makes them all look smooth.
    cache = {}
    for level, count in enumerate(SPIN_FRAMES, 1):
        step = SPIN_SPAN / count
        assert abs(step * count - SPIN_SPAN) < 1e-9, \
            "%d frames do not divide %g degrees evenly" % (count, SPIN_SPAN)

        frames = []
        for i in range(count):
            angle = i * step
            if angle not in cache:
                # RESTING artwork, not pressed. Holding both buttons is its own
                # state, not a longer click: the spinner should read as the
                # normal pointer in motion. For the 8 Star that means the thin
                # outline turning rather than the heavy interlaced band, which
                # at speed became a blob.
                shapes, over = role_shapes(variant, "Wait", False, angle)
                cache[angle] = cur_bytes(
                    {s: render(shapes, over, s, False) for s in ANI_SIZES})
            frames.append(cache[angle])

        p = os.path.join(out, "spin%d.ani" % level)
        open(p, "wb").write(ani_bytes(frames, SPIN_JIF))
        made.append(p)
    return made


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="build G9 cursor sets")
    ap.add_argument("--variant", default="all",
                    choices=["all", "reticle", "octagram"])
    ap.add_argument("--colour-a", help="the cyan slot, #RRGGBB")
    ap.add_argument("--colour-b", help="the ginger slot, #RRGGBB")
    ap.add_argument("--rest-close", type=float,
                    help="reticle bracket inset at rest, 0 to 0.6")
    ap.add_argument("--press-close", type=float,
                    help="reticle bracket inset while held, 0 to 0.6")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="0.25 to 1.0, how much of the box the mark fills")
    args = ap.parse_args()

    set_colours(args.colour_a, args.colour_b)
    set_scale(args.scale)
    set_spacing(args.rest_close, args.press_close)
    wanted = ("reticle", "octagram") if args.variant == "all" else (args.variant,)
    for variant in wanted:
        for pressed, sub in ((False, "resting"), (True, "pressed")):
            files = build(variant, pressed, sub)
            kb = sum(os.path.getsize(f) for f in files) / 1024.0
            print("%-9s %-8s %2d files %7.1f KB" % (variant, sub, len(files), kb))
        spun = build_spin(variant)
        kb = sum(os.path.getsize(f) for f in spun) / 1024.0
        print("%-9s %-8s %2d files %7.1f KB" % (variant, "spin", len(spun), kb))
    print()
    print("-> " + OUT_DIR)
