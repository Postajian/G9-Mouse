"""JSON control surface for the G9 PC Control cursor panel.

The panel is PowerShell WinForms and the cursor engine is Python, so this is the
one seam between them. Every command prints a single JSON object on stdout and
nothing else, so the PowerShell side can ConvertFrom-Json without parsing noise.

    python cursor_cli.py status
    python cursor_cli.py preview --variant octagram --colour-a #00d9ff --colour-b #ff7a18
    python cursor_cli.py apply   --variant octagram --colour-a #00d9ff --colour-b #ff7a18 --size 32
    python cursor_cli.py default      back to the pointers you had before
    python cursor_cli.py stock        plain Windows pointers
    python cursor_cli.py effect --state on|off
"""

import argparse
import json
import os
import subprocess
import sys
import winreg

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HERE, "panel_settings.json")
PREVIEW = os.path.join(HERE, "panel_preview.png")

DEFAULTS = {"variant": "octagram", "colourA": "#00d9ff", "colourB": "#ff7a18",
            "size": 32, "effect": False, "restClose": 17, "pressClose": 27,
            "trail": None}      # filled from TRAIL_DEFAULTS by load_settings

# Reticle bracket spacing, as whole percents pulled toward the centre. Any
# value in range is accepted - the panel lets you type one - and this list is
# only the handful worth suggesting.
CLOSES = [0, 5, 10, 17, 22, 27, 33, 40, 50]   # 17 and 27 are the chosen pair
CLOSE_MIN, CLOSE_MAX = 0, 60


SIZE_MIN, SIZE_MAX = 12, 256

# The light tail, as ONE table: the panel builds its boxes from this, the CLI
# builds its arguments from it, and the helper clamps with it. Adding a knob is
# a row here and nothing else. Every entry is a number the user types, per
# RED RULE 10 - the only picked values are the two colours, which come from the
# Windows colour dialog because a colour is not a number on a scale.
#   key, label, min, max, suffix, help
TRAIL_SPEC = [
    ("ms",        "LENGTH",    40,  2000, "ms",
     "How far back the tail reaches, in milliseconds of travel. Longer = longer streak."),
    ("core",      "CORE",       1,    24, "px",
     "Width of the bright inner line."),
    ("glow",      "GLOW",       1,    48, "px",
     "Width of the soft outer bloom. Below the core width the glow disappears."),
    ("gap",       "SPREAD",     0,    40, "px",
     "Half the distance between the two rails. 0 draws them as one line."),
    ("contrast",  "CONTRAST",  10,   200, "%",
     "How strongly the tail burns against the screen. 100 is the default mix."),
]
TRAIL_COLOURS = [
    ("coreColour", "CORE HUE", "The bright centre of the tail."),
    ("glowColour", "GLOW HUE", "The soft bloom around it."),
]
TRAIL_DEFAULTS = {"ms": 300, "core": 3, "glow": 11, "gap": 7, "contrast": 100,
                  "coreColour": "#eef8ff", "glowColour": "#82c3ff"}


def clamp_trail(raw):
    """Take whatever the panel sent and return a complete, in-range tail.

    Never raises and never returns a partial dict: the helper reads this on a
    file change, and a half-written settings file must degrade to the default
    tail rather than stop the overlay."""
    out_ = dict(TRAIL_DEFAULTS)
    if isinstance(raw, dict):
        for key, _lab, lo, hi, _sfx, _help in TRAIL_SPEC:
            if key in raw:
                try:
                    out_[key] = max(lo, min(hi, int(round(float(raw[key])))))
                except (TypeError, ValueError):
                    pass
        for key, _lab, _help in TRAIL_COLOURS:
            v = str(raw.get(key, "")).strip()
            if len(v) == 7 and v[0] == "#":
                try:
                    int(v[1:], 16)
                    out_[key] = v.lower()
                except ValueError:
                    pass
    return out_




def clamp_size(v):
    """Any typed pointer size, clamped to what Windows will actually render."""
    return max(SIZE_MIN, min(SIZE_MAX, int(round(float(v)))))


def clamp_close(v):
    """Accept any typed number, but never outside what the shape survives."""
    return max(CLOSE_MIN, min(CLOSE_MAX, int(round(float(v)))))
SIZES = [20, 24, 28, 32, 40, 48, 64, 96, 128]
WINDOWS_MIN = 32        # measured: CursorBaseSize below this is silently clamped

# One named constant rather than a literal at each use. A path spelled inline
# here once arrived with its \t turned into a real tab, which only showed up as
# the preview quietly falling back to a bitmap font inside a try/except.
TIMES = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "times.ttf")


def size_plan(px):
    """Turn a wanted on-screen size into (CursorBaseSize, artwork scale).

    Windows will not make the cursor surface smaller than 32 px, so anything
    below that is delivered by drawing a smaller mark inside a 32 px cursor
    instead. Above 32 the surface itself grows and the mark fills it.
    """
    px = int(px)
    if px < WINDOWS_MIN:
        return WINDOWS_MIN, px / float(WINDOWS_MIN)
    return px, 1.0

import build_cursors as B          # noqa: E402  - after HERE is known
import install_cursors as I        # noqa: E402


def out(**kw):
    print(json.dumps(kw))
    return 0


def load_settings():
    s = dict(DEFAULTS)
    if os.path.isfile(SETTINGS):
        try:
            s.update(json.load(open(SETTINGS, encoding="utf-8")))
        except (OSError, ValueError):
            pass
    # A saved name that no longer exists - a set renamed or dropped between
    # versions - must not take the whole engine down with it. Renaming
    # a set renamed twice left this file pointing at a deleted folder and
    # every command after it failed.
    if s.get("variant") not in B.VARIANTS:
        s["variant"] = DEFAULTS["variant"]
    s["size"] = clamp_size(s.get("size", DEFAULTS["size"]))
    s["trail"] = clamp_trail(s.get("trail"))
    return s


def save_settings(s):
    json.dump(s, open(SETTINGS, "w", encoding="utf-8"), indent=2)


def effect_pid():
    """PID of a running click helper, or None. Verified against the OS, not just
    the pid file, so a stale file after a hard kill does not read as running."""
    pf = os.path.join(HERE, "click_effect.pid")
    if not os.path.isfile(pf):
        return None
    try:
        pid = int(open(pf).read().strip())
    except (OSError, ValueError):
        return None
    r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH", "/FO", "CSV"],
                       capture_output=True, text=True)
    return pid if ("python" in r.stdout.lower()) else None


def base_size():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, I.KEY) as k:
            return int(winreg.QueryValueEx(k, "CursorBaseSize")[0])
    except OSError:
        return 32


def set_base_size(px):
    """Windows scales the pointer from CursorBaseSize, so changing the size is a
    registry value plus a reload - not a re-render. Our .cur carries 32 to 128,
    so every offered size is a real baked image rather than an upscale."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, I.KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "CursorBaseSize", 0, winreg.REG_DWORD, int(px))
    I.apply_now()


def current_variant():
    cfg = {k: v["value"] for k, v in I.read_current().items()}
    seen = set()
    for role in I.ROLES:
        p = cfg.get(role, "")
        if p:
            parts = p.replace("/", "\\").split("\\")
            if "cursors" in parts:
                seen.add(parts[parts.index("cursors") + 1])
    if not seen:
        return "windows"
    return seen.pop() if len(seen) == 1 else "mixed"


def cmd_status(_a):
    s = load_settings()
    cfg = {k: v["value"] for k, v in I.read_current().items()}
    # A sub-32 choice lives in the artwork, not in CursorBaseSize, so the
    # registry would report 32 and the panel would forget what was picked.
    shown = base_size()
    if shown == WINDOWS_MIN and int(s.get("size", 32)) < WINDOWS_MIN:
        shown = int(s["size"])
    return out(ok=True, variant=current_variant(), saved=s, size=shown,
               # The panel builds its set buttons from THIS list. It used to
               # hard-code two, so adding a third set drew it in the gallery
               # with no button to pick it.
               variants=[{"id": v, "label": label_for(v)} for v in B.VARIANTS],
               sizes=SIZES, closes=CLOSES,
               sizeMin=SIZE_MIN, sizeMax=SIZE_MAX,
               trail=s["trail"],
               trailSpec=[{"key": k, "label": lab, "min": lo, "max": hi,
                           "suffix": sfx, "help": hlp}
                          for k, lab, lo, hi, sfx, hlp in TRAIL_SPEC],
               trailColours=[{"key": k, "label": lab, "help": hlp}
                             for k, lab, hlp in TRAIL_COLOURS],
               restClose=clamp_close(s["restClose"]),
               pressClose=clamp_close(s["pressClose"]),
               closeMin=CLOSE_MIN, closeMax=CLOSE_MAX,
               effect=bool(effect_pid()),
               customRoles=[r for r in I.ROLES if cfg.get(r, "")],
               scheme=cfg.get("", "") or "(none)")


GALLERY = os.path.join(HERE, "panel_gallery.png")

# Display names. A variant with no entry falls back to its folder name, so a new
# set still appears rather than being skipped.
VARIANT_LABELS = {"reticle": "RETICLE", "octagram": "8 STAR",
                  "arrow": "ARROW"}


def label_for(variant):
    """Display name, falling back to the folder name so a set added to
    build_cursors.VARIANTS appears even before it is named here."""
    return VARIANT_LABELS.get(variant, variant.upper())


def render_preview(variant, a, b, size=32, rest=None, press=None):
    """Resting vs clicked for ONE set, plus true size. The panel shows the
    gallery instead; this stays for the CLI and for tests."""
    from PIL import Image, ImageDraw, ImageFont

    base, scale = size_plan(size)
    B.set_colours(a, b)
    B.set_scale(scale)
    B.set_spacing(None if rest is None else rest / 100.0,
                  None if press is None else press / 100.0)
    try:
        tiles = []
        for pressed in (False, True):
            shapes, over = B.role_shapes(variant, "Arrow", pressed)
            tiles.append((B.render(shapes, over, 128, False),
                          B.render(shapes, over, base, False)))
    finally:
        B.set_scale(1.0)

    one = tiles[0][1]
    w, h = 300, 190 + one.height
    sheet = Image.new("RGB", (w, h), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    try:
        f = ImageFont.truetype(TIMES, 14)
        fs = ImageFont.truetype(TIMES, 11)
    except OSError:
        f = fs = ImageFont.load_default()

    for i, (big, _real) in enumerate(tiles):
        x = 8 + i * 146
        d.rectangle([x, 8, x + 136, 144], outline=(90, 90, 90))
        sheet.paste(big.resize((124, 124), Image.LANCZOS), (x + 6, 14),
                    big.resize((124, 124), Image.LANCZOS))
        d.text((x + 68, 148), ("RESTING", "CLICKED")[i], font=f, anchor="ma",
               fill=(212, 175, 55))

    sw = one.width + 6
    sx, sy = w // 2 - sw, 172
    d.rectangle([sx, sy, sx + sw, sy + sw], fill=(242, 242, 242))
    d.rectangle([sx + sw, sy, sx + 2 * sw, sy + sw], fill=(10, 10, 10))
    sheet.paste(one, (sx + 3, sy + 3), one)
    sheet.paste(one, (sx + sw + 3, sy + 3), one)
    d.text((w // 2, sy + sw + 2), "actual size", font=fs, anchor="ma",
           fill=(150, 150, 150))
    sheet.save(PREVIEW)
    return PREVIEW


def render_gallery(a, b, size=32, rest=None, press=None, variant="octagram"):
    from PIL import Image, ImageDraw, ImageFont
    base, scale = size_plan(size)
    B.set_colours(a, b)
    B.set_scale(scale)
    B.set_spacing(None if rest is None else rest / 100.0,
                  None if press is None else press / 100.0)
    try:
        roles = B.ROLES_STATIC + ["Wait", "AppStarting"]
        cols, cell, lab = 5, 62, 16          # 5 x 3 = the 15 roles, compactly
        rows = (len(roles) + cols - 1) // cols
        block_w = cols * cell
        rule = 9                              # gap plus the divider rule
        width = len(B.VARIANTS) * block_w + (len(B.VARIANTS) - 1) * rule

        # Compact header: the live set resting vs clicked, plus true size.
        hero = []
        for pressed in (False, True):
            sh, ov = B.role_shapes(variant, "Arrow", pressed)
            hero.append((B.render(sh, ov, 128, False),
                         B.render(sh, ov, base, False)))
        one = hero[0][1]
        hdr = 104 + one.height + 44   # clear of the true-size caption

        height = hdr + 18 + rows * (cell + lab) + 8
        sheet = Image.new("RGB", (width, height), (20, 20, 20))
        d = ImageDraw.Draw(sheet)
        try:
            f = ImageFont.truetype(TIMES, 13)
            fs = ImageFont.truetype(TIMES, 10)
        except OSError:
            f = fs = ImageFont.load_default()

        for i, (big, _real) in enumerate(hero):
            x = width // 2 - 104 + i * 108
            d.rectangle([x, 8, x + 96, 104], outline=(90, 90, 90))
            small = big.resize((84, 84), Image.LANCZOS)
            sheet.paste(small, (x + 6, 14), small)
            d.text((x + 48, 106), ("RESTING", "CLICKED")[i], font=fs,
                   anchor="ma", fill=(212, 175, 55))
        sw = one.width + 6
        sx, sy = width // 2 - sw, 122
        d.rectangle([sx, sy, sx + sw, sy + sw], fill=(242, 242, 242))
        d.rectangle([sx + sw, sy, sx + 2 * sw, sy + sw], fill=(10, 10, 10))
        d.rectangle([sx, sy, sx + 2 * sw, sy + sw], outline=(90, 90, 90))
        sheet.paste(one, (sx + 3, sy + 3), one)
        sheet.paste(one, (sx + sw + 3, sy + 3), one)
        d.text((width // 2, sy + sw + 2), "actual size", font=fs,
               anchor="ma", fill=(150, 150, 150))

        live = 0
        for vi, name in enumerate(B.VARIANTS):
            gone = B.removed_roles(name)
            bx = vi * (block_w + rule)
            if vi:
                # A rule between blocks, so three grids do not read as one.
                d.line([(bx - rule // 2, hdr), (bx - rule // 2, height - 6)],
                       fill=(62, 62, 62))
            d.text((bx + block_w // 2, hdr),
                   "%s  %d/%d" % (VARIANT_LABELS.get(name, name.upper()),
                                  len(roles) - len(gone), len(roles)),
                   font=f, anchor="ma", fill=(212, 175, 55))

            for i, role in enumerate(roles):
                x = bx + (i % cols) * cell
                y = hdr + 18 + (i // cols) * (cell + lab)
                d.rectangle([x + 2, y + 1, x + cell - 3, y + cell - 2],
                            outline=(46, 46, 46) if role in gone else (78, 78, 78))
                if role in gone:
                    d.text((x + cell // 2, y + cell // 2 - 7), "gone",
                           font=fs, anchor="ma", fill=(120, 88, 88))
                else:
                    live += 1
                    shapes, over = B.role_shapes(name, role, False)
                    hg = "ink" if role == "Help" else False
                    img = B.render(shapes, over, 128, False, help_glyph=hg)
                    img = img.resize((cell - 14, cell - 14), Image.LANCZOS)
                    sheet.paste(img, (x + 7, y + 5), img)
                d.text((x + cell // 2, y + cell - 1), role, font=fs, anchor="ma",
                       fill=(210, 210, 210) if role not in gone else (105, 105, 105))
        sheet.save(GALLERY)
    finally:
        B.set_scale(1.0)
    return GALLERY, live


def cmd_gallery(a):
    s = load_settings()
    path, live = render_gallery(
        a.colour_a or s["colourA"], a.colour_b or s["colourB"],
        int(a.size or s["size"]),
        a.rest_close if a.rest_close is not None else s["restClose"],
        a.press_close if a.press_close is not None else s["pressClose"],
        a.variant or s["variant"])
    return out(ok=True, gallery=path, live=live)


def cmd_preview(a):
    s = load_settings()
    v = a.variant or s["variant"]
    ca = a.colour_a or s["colourA"]
    cb = a.colour_b or s["colourB"]
    px = int(a.size or s["size"])
    rest = a.rest_close if a.rest_close is not None else s["restClose"]
    press = a.press_close if a.press_close is not None else s["pressClose"]
    path = render_preview(v, ca, cb, px, rest, press)
    return out(ok=True, preview=path, variant=v, colourA=ca, colourB=cb, size=px)


def cmd_apply(a):
    s = load_settings()
    v = a.variant or s["variant"]
    ca = a.colour_a or s["colourA"]
    cb = a.colour_b or s["colourB"]
    size = int(a.size or s["size"])
    rest = a.rest_close if a.rest_close is not None else s["restClose"]
    press = a.press_close if a.press_close is not None else s["pressClose"]
    if v not in B.VARIANTS:
        return out(ok=False, error="unknown variant: %s" % v)

    was_running = bool(effect_pid())
    if was_running:
        stop_effect()

    base, scale = size_plan(size)
    B.set_colours(ca, cb)
    B.set_scale(scale)
    B.set_spacing(rest / 100.0, press / 100.0)
    try:
        for pressed, sub in ((False, "resting"), (True, "pressed")):
            B.build(v, pressed, sub)
        # The both-buttons spinner is drawn from the same colours and scale, so
        # it has to be rebuilt here too or it would keep the previous look.
        B.build_spin(v)
    finally:
        B.set_scale(1.0)

    I.install(v)
    set_base_size(base)

    s.update({"variant": v, "colourA": ca, "colourB": cb, "size": size,
              "restClose": rest, "pressClose": press})
    save_settings(s)
    if was_running:
        start_effect()
    return out(ok=True, variant=v, colourA=ca, colourB=cb, size=size,
               restClose=rest, pressClose=press,
               effect=bool(effect_pid()))


def cmd_default(_a):
    """Back to the pointers from before this tool was ever run.

    NOT the newest restore point: auto-apply writes one per change, so the
    newest is itself a G9 set. install_cursors pins the true original once.
    """
    if effect_pid():
        stop_effect()
    kind = I.restore_original()
    return out(ok=True, variant=current_variant(), restored=kind)


def cmd_stock(_a):
    if effect_pid():
        stop_effect()
    I.windows_stock()
    return out(ok=True, variant=current_variant(), restored="windows")


def start_effect():
    # Clear any stale pid first. Otherwise the readiness check below can read a
    # dead pid left by a hard kill, decide the helper is not running, and report
    # failure while the one just launched is alive and well.
    try:
        os.remove(os.path.join(HERE, "click_effect.pid"))
    except OSError:
        pass
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(exe):
        exe = sys.executable
    subprocess.Popen([exe, os.path.join(HERE, "click_effect.py")],
                     cwd=HERE, creationflags=0x08000000)   # CREATE_NO_WINDOW


def stop_effect():
    pid = effect_pid()
    if pid:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    I.apply_now()          # make sure nothing is left stuck in the pressed set
    try:
        os.remove(os.path.join(HERE, "click_effect.pid"))
    except OSError:
        pass


def cmd_effect(a):
    if a.state == "on":
        if not effect_pid():
            start_effect()
    elif a.state == "off":
        stop_effect()
    # Poll rather than sleep a fixed amount: the helper writes its pid file only
    # after Python has started and imported, which took longer than the old flat
    # 0.4 s and made a successful start report as a failure.
    import time
    deadline = time.time() + 5.0
    while time.time() < deadline:
        running = bool(effect_pid())
        if running == (a.state == "on") or a.state == "status":
            break
        time.sleep(0.15)
    running = bool(effect_pid())
    s = load_settings()
    s["effect"] = running
    save_settings(s)
    return out(ok=True, effect=running)



TRAIL_PREVIEW = os.path.join(HERE, "panel_trail.png")

# A typical fast swipe, used to turn the tail's LENGTH (a time) into a streak
# length (a distance) for the still preview. Measured by hand: a deliberate
# flick across a monitor runs about this fast.
PREVIEW_SPEED = 1200.0      # pixels per second


def render_trail_preview(cfg):
    """Draw the tail as it will really look, and show what each knob controls.

    Two halves, because a streak alone cannot show a width. Left is a magnified
    CROSS-SECTION - a slice straight through the tail - where core, glow and
    spread are visible as sizes you can compare. Right is the tail itself at
    true scale, with its length marked.

    Deliberately rendered through neon_trail's own _bitmap rather than a
    lookalike: a preview drawn by separate code shows what someone THINKS the
    setting does. This is the code that paints your screen, composited over a
    dark strip, so a wrong preview means a wrong tail.
    """
    import math

    from PIL import Image, ImageChops, ImageDraw, ImageFont

    import neon_trail as NT

    W, H = 660, 170
    BG = (18, 18, 18)
    INK = (150, 158, 168)
    LIT = (236, 214, 120)
    WARN = (255, 150, 90)
    SPLIT = 250
    BAR_X0, BAR_X1 = 22, 128           # the slice is this wide
    TICK = BAR_X1 + 6                  # brackets hang off the right of it

    trail = NT.from_settings(cfg)
    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    try:
        f = ImageFont.truetype(TIMES, 12)
        fs = ImageFont.truetype(TIMES, 11)
    except OSError:
        f = fs = ImageFont.load_default()

    core_rgb = NT.hex_rgb(cfg.get("coreColour"), (238, 248, 255))
    glow_rgb = NT.hex_rgb(cfg.get("glowColour"), (130, 195, 255))
    gap, core, glow = cfg["gap"], cfg["core"], cfg["glow"]
    contrast = cfg["contrast"] / 100.0

    # ------------------------------------------------------- cross-section
    d.text((BAR_X0, 6), "A SLICE THROUGH THE TAIL", font=fs, fill=INK)
    reach = gap + max(core, glow) / 2.0
    # Bounded so the slice can never grow into the captions below it, at any
    # combination of the knobs. SPREAD 0 with a fat glow used to print its
    # caption straight through the CONTRAST line.
    # Shrinks as well as magnifies. Floored at 1.0 it could not fit the widest
    # tail - SPREAD 40 with GLOW 48 drew straight through the title.
    zoom = max(0.2, min(6.0, 30.0 / max(1.0, reach)))
    mid = 62
    rails = [mid - gap * zoom, mid + gap * zoom] if gap else [mid]

    def band(y, half, rgb, k):
        c = tuple(min(255, int(v * k)) for v in rgb)
        d.rectangle([BAR_X0, y - half, BAR_X1, y + half], fill=c)

    for ry in rails:
        band(ry, glow * zoom / 2.0, glow_rgb, min(1.0, 0.30 * contrast + 0.18))
        band(ry, core * zoom / 2.0, core_rgb, min(1.0, 0.92 * contrast))

    def bracket(ya, yb, text, colour=LIT):
        d.line([(TICK, ya), (TICK, yb)], fill=INK)
        d.line([(TICK, ya), (TICK - 4, ya)], fill=INK)
        d.line([(TICK, yb), (TICK - 4, yb)], fill=INK)
        d.text((TICK + 6, (ya + yb) / 2 - 8), text, font=f, fill=colour)

    # One band per rail when there are two, so the labels never sit on top of
    # each other; the bands are identical on both rails anyway.
    captions = []
    if len(rails) == 2:
        bracket(rails[0] - glow * zoom / 2.0, rails[0] + glow * zoom / 2.0,
                "GLOW %d px" % glow)
        bracket(rails[1] - core * zoom / 2.0, rails[1] + core * zoom / 2.0,
                "CORE %d px" % core)
        d.line([(BAR_X0 - 8, rails[0]), (BAR_X0 - 8, rails[1])], fill=INK)
        captions.append(("SPREAD %d px apart" % gap, LIT, f))
    else:
        bracket(mid - glow * zoom / 2.0, mid + glow * zoom / 2.0, "GLOW %d px" % glow)
        captions.append(("CORE %d px inside it" % core, LIT, f))
        captions.append(("SPREAD 0  -  one line, not two", LIT, f))

    captions.append(("CONTRAST %d%%  -  how hard it burns" % cfg["contrast"], LIT, f))
    if glow <= core:
        # Worth saying out loud: the glow is doing nothing at these numbers.
        captions.append(("glow is inside the core, so it cannot be seen", WARN, fs))

    # Stacked from one cursor rather than placed at fixed offsets, so the count
    # of lines can change without two of them landing on the same row.
    cap_y = min(mid + reach * zoom + 12, H - 16 * len(captions) - 8)
    for text, colour, font in captions:
        d.text((BAR_X0, cap_y), text, font=font, fill=colour)
        cap_y += 16
    d.line([(SPLIT, 6), (SPLIT, H - 6)], fill=(70, 70, 70))

    # ------------------------------------------------------- the real thing
    d.text((SPLIT + 18, 6), "AT TRUE SIZE, BEHIND THE POINTER", font=fs, fill=INK)
    head_x, head_y = W - 46, 70
    streak = max(26.0, min(W - SPLIT - 96.0, cfg["ms"] / 1000.0 * PREVIEW_SPEED))
    n = 26
    pts = [(head_x - streak * (1.0 - i / float(n - 1)),
            head_y + 12.0 * math.sin((1.0 - i / float(n - 1)) * 2.2))
           for i in range(n)]

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    pad = NT.MARGIN
    x0, y0 = int(min(xs) - pad), int(min(ys) - pad)
    bw, bh = int(max(xs) - min(xs)) + pad * 2, int(max(ys) - min(ys)) + pad * 2
    layer = trail._bitmap(pts, x0, y0, bw, bh)

    patch = sheet.crop((x0, y0, x0 + bw, y0 + bh))
    inv = ImageChops.invert(layer.getchannel("A"))
    # Premultiplied source over background: out = src + bg * (1 - alpha).
    under = Image.merge("RGB", [ImageChops.multiply(c, inv) for c in patch.split()])
    over = Image.merge("RGB", [ImageChops.add(a, b) for a, b in
                               zip(layer.convert("RGB").split(), under.split())])
    sheet.paste(over, (x0, y0))

    d = ImageDraw.Draw(sheet)
    d.ellipse([head_x - 3, head_y - 3, head_x + 3, head_y + 3],
              fill=(255, 255, 255), outline=(40, 40, 40))

    ya, xa, xb = H - 40, head_x - streak, head_x
    d.line([(xa, ya), (xb, ya)], fill=INK)
    for x, dx in ((xa, 4), (xb, -4)):
        d.line([(x, ya), (x + dx, ya - 3)], fill=INK)
        d.line([(x, ya), (x + dx, ya + 3)], fill=INK)
        d.line([(x, ya - 5), (x, ya + 5)], fill=INK)
    d.text(((xa + xb) / 2, ya + 5), "LENGTH %d ms" % cfg["ms"], font=f,
           anchor="ma", fill=LIT)
    d.text((W - 8, H - 20), "at a fast swipe", font=fs, anchor="ra", fill=INK)

    d.rectangle([0, 0, W - 1, H - 1], outline=(70, 70, 70))
    sheet.save(TRAIL_PREVIEW)
    return TRAIL_PREVIEW


def cmd_trail(a):
    """Write the tail settings. The helper notices the file changed and picks
    them up on its next frame, so there is nothing to restart."""
    s = load_settings()
    t = dict(s["trail"])
    for key, _lab, _lo, _hi, _sfx, _help in TRAIL_SPEC:
        v = getattr(a, key, None)
        if v is not None:
            t[key] = v
    for key, _lab, _help in TRAIL_COLOURS:
        v = getattr(a, key, None)
        if v:
            t[key] = v
    s["trail"] = clamp_trail(t)
    save_settings(s)
    # Rendered on every change, so the panel can show what the numbers did
    # rather than asking the user to spin the mouse and guess.
    try:
        shot = render_trail_preview(s["trail"])
    except Exception:
        shot = ""
    return out(ok=True, trail=s["trail"], preview=shot)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "default", "stock"):
        sub.add_parser(name)
    for name in ("preview", "apply", "gallery"):
        p = sub.add_parser(name)
        p.add_argument("--variant", choices=B.VARIANTS)
        p.add_argument("--colour-a")
        p.add_argument("--colour-b")
        p.add_argument("--size", type=clamp_size)
        p.add_argument("--rest-close", type=clamp_close)
        p.add_argument("--press-close", type=clamp_close)
    pt = sub.add_parser("trail")
    for key, _lab, _lo, _hi, _sfx, _help in TRAIL_SPEC:
        pt.add_argument("--" + key, type=float)     # clamped in cmd_trail
    for key, _lab, _help in TRAIL_COLOURS:
        pt.add_argument("--" + key.replace("Colour", "-colour"))

    pe = sub.add_parser("effect")
    pe.add_argument("--state", choices=["on", "off", "status"], default="status")

    a = ap.parse_args()
    try:
        return {"status": cmd_status, "preview": cmd_preview, "apply": cmd_apply,
                "default": cmd_default, "stock": cmd_stock,
                "gallery": cmd_gallery,
                "effect": cmd_effect, "trail": cmd_trail}[a.cmd](a)
    except Exception as e:                      # the panel must always get JSON
        return out(ok=False, error="%s: %s" % (type(e).__name__, e))


if __name__ == "__main__":
    sys.exit(main())
