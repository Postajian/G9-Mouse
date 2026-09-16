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
            "size": 32, "effect": False, "restClose": 17, "pressClose": 27}

# Reticle bracket spacing, as whole percents pulled toward the centre. Any
# value in range is accepted - the panel lets you type one - and this list is
# only the handful worth suggesting.
CLOSES = [0, 5, 10, 17, 22, 27, 33, 40, 50]   # 17 and 27 are the chosen pair
CLOSE_MIN, CLOSE_MAX = 0, 60


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
               sizes=SIZES, closes=CLOSES,
               restClose=clamp_close(s["restClose"]),
               pressClose=clamp_close(s["pressClose"]),
               closeMin=CLOSE_MIN, closeMax=CLOSE_MAX,
               effect=bool(effect_pid()),
               customRoles=[r for r in I.ROLES if cfg.get(r, "")],
               scheme=cfg.get("", "") or "(none)")


def render_preview(variant, a, b, size=32, rest=None, press=None):
    from PIL import Image, ImageDraw, ImageFont
    base, scale = size_plan(size)
    B.set_colours(a, b)
    B.set_scale(scale)
    B.set_spacing(None if rest is None else rest / 100.0,
                  None if press is None else press / 100.0)
    tiles, real = [], []
    for pressed in (False, True):
        shapes, over = B.role_shapes(variant, "Arrow", pressed)
        tiles.append(B.render(shapes, over, 128, False))
        real.append(B.render(shapes, over, base, False))
    B.set_scale(1.0)

    one = real[0]
    # Height follows the true-size strip: at 128 px that strip is taller than the
    # blown-up tiles, and a fixed height would have clipped it.
    strip_y = 186
    w = 150 * 2
    h = strip_y + one.height + 36

    sheet = Image.new("RGB", (w, h), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    try:
        f = ImageFont.truetype(TIMES, 15)
        fs = ImageFont.truetype(TIMES, 12)
    except OSError:
        f = fs = ImageFont.load_default()

    for i, (img, label) in enumerate(zip(tiles, ("RESTING", "CLICKED"))):
        x = i * 150
        d.rectangle([x + 8, 8, x + 141, 141], outline=(90, 90, 90))
        # paste needs the mask at the pasted size, not the original
        small = img.resize((120, 120), Image.LANCZOS)
        sheet.paste(small, (x + 15, 15), small)
        d.text((x + 75, 146), label, font=f, anchor="ma", fill=(212, 175, 55))

    d.text((w // 2, 166), "click or hold the mouse to swap", font=fs, anchor="ma",
           fill=(150, 150, 150))

    # True size, once, centred, on light and on dark. Without it the panel would
    # only ever show a flattering blow-up, and a size that turns to mush at its
    # real size would look fine right up until it was installed.
    cell = one.width + 8
    sx = w // 2 - cell
    d.rectangle([sx, strip_y, sx + cell, strip_y + cell], fill=(242, 242, 242))
    d.rectangle([sx + cell, strip_y, sx + 2 * cell, strip_y + cell], fill=(10, 10, 10))
    d.rectangle([sx, strip_y, sx + 2 * cell, strip_y + cell], outline=(90, 90, 90))
    sheet.paste(one, (sx + 4, strip_y + 4), one)
    sheet.paste(one, (sx + cell + 4, strip_y + 4), one)
    d.text((w // 2, strip_y + cell + 8), "actual size on screen", font=fs,
           anchor="ma", fill=(150, 150, 150))

    sheet.save(PREVIEW)
    return PREVIEW


GALLERY = os.path.join(HERE, "panel_gallery.png")


def render_gallery(a, b, size=32, rest=None, press=None, variant="octagram"):
    """Every role of both variants on one sheet, drawn from live geometry.

    Deleted roles are drawn as an empty slot rather than skipped, so the sheet
    shows what is GONE as well as what is there - a silently shorter grid would
    hide the difference between a removed role and a rendering bug.
    """
    from PIL import Image, ImageDraw, ImageFont

    base, scale = size_plan(size)
    B.set_colours(a, b)
    B.set_scale(scale)
    B.set_spacing(None if rest is None else rest / 100.0,
                  None if press is None else press / 100.0)
    try:
        roles = B.ROLES_STATIC + ["Wait", "AppStarting"]
        cols, cell, head = 8, 120, 30
        rows_per = (len(roles) + cols - 1) // cols
        block = head + rows_per * cell
        width = cols * cell

        # Header: the live variant resting vs clicked, plus true size. It
        # used to be a separate image in a separate control; folding it in
        # here is what lets the panel drop the ALL VERSIONS button.
        hero = []
        for pressed in (False, True):
            sh, ov = B.role_shapes(variant, "Arrow", pressed)
            hero.append((B.render(sh, ov, 128, False),
                         B.render(sh, ov, base, False)))
        hdr = 150 + hero[0][1].height + 56   # clear of the true-size caption

        sheet = Image.new("RGB", (width, hdr + block * 2 + 10), (20, 20, 20))
        d = ImageDraw.Draw(sheet)
        try:
            f = ImageFont.truetype(TIMES, 15)
            fs = ImageFont.truetype(TIMES, 11)
        except OSError:
            f = fs = ImageFont.load_default()

        for i, (big, real) in enumerate(hero):
            x = width // 2 - 150 + i * 156
            d.rectangle([x, 12, x + 144, 140], outline=(90, 90, 90))
            small = big.resize((116, 116), Image.LANCZOS)
            sheet.paste(small, (x + 14, 18), small)
            d.text((x + 72, 144), ("RESTING", "CLICKED")[i], font=f,
                   anchor="ma", fill=(212, 175, 55))
        one = hero[0][1]
        sw = one.width + 8
        sx, sy = width // 2 - sw, 168
        d.rectangle([sx, sy, sx + sw, sy + sw], fill=(242, 242, 242))
        d.rectangle([sx + sw, sy, sx + 2 * sw, sy + sw], fill=(10, 10, 10))
        d.rectangle([sx, sy, sx + 2 * sw, sy + sw], outline=(90, 90, 90))
        sheet.paste(one, (sx + 4, sy + 4), one)
        sheet.paste(one, (sx + sw + 4, sy + 4), one)
        d.text((width // 2, sy + sw + 6), "actual size on screen",
               font=fs, anchor="ma", fill=(150, 150, 150))

        live = 0
        for vi, (variant, label) in enumerate(
                (("reticle", "A .  RETICLE"), ("octagram", "B .  8 STAR"))):
            gone = B.removed_roles(variant)
            top = hdr + vi * block + 5
            d.text((cols * cell // 2, top + 4),
                   "%s   %d of %d" % (label, len(roles) - len(gone), len(roles)),
                   font=f, anchor="ma", fill=(212, 175, 55))
            for i, role in enumerate(roles):
                x = (i % cols) * cell
                y = top + head + (i // cols) * cell
                d.rectangle([x + 4, y + 2, x + cell - 5, y + cell - 8],
                            outline=(46, 46, 46) if role in gone else (84, 84, 84))
                if role in gone:
                    d.text((x + cell // 2, y + cell // 2 - 18), "removed",
                           font=fs, anchor="ma", fill=(120, 88, 88))
                else:
                    live += 1
                    shapes, over = B.role_shapes(variant, role, False)
                    hg = "ink" if role == "Help" else False
                    img = B.render(shapes, over, 128, False, help_glyph=hg)
                    img = img.resize((72, 72), Image.LANCZOS)
                    sheet.paste(img, (x + cell // 2 - 36, y + 8), img)
                d.text((x + cell // 2, y + cell - 24), role, font=fs, anchor="ma",
                       fill=(215, 215, 215) if role not in gone else (110, 110, 110))
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
    if v not in ("reticle", "octagram"):
        return out(ok=False, error="apply needs reticle or octagram")

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


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "default", "stock"):
        sub.add_parser(name)
    for name in ("preview", "apply", "gallery"):
        p = sub.add_parser(name)
        p.add_argument("--variant", choices=["reticle", "octagram"])
        p.add_argument("--colour-a")
        p.add_argument("--colour-b")
        p.add_argument("--size", type=int, choices=SIZES)
        p.add_argument("--rest-close", type=clamp_close)
        p.add_argument("--press-close", type=clamp_close)
    pe = sub.add_parser("effect")
    pe.add_argument("--state", choices=["on", "off", "status"], default="status")

    a = ap.parse_args()
    try:
        return {"status": cmd_status, "preview": cmd_preview, "apply": cmd_apply,
                "default": cmd_default, "stock": cmd_stock,
                "gallery": cmd_gallery,
                "effect": cmd_effect}[a.cmd](a)
    except Exception as e:                      # the panel must always get JSON
        return out(ok=False, error="%s: %s" % (type(e).__name__, e))


if __name__ == "__main__":
    sys.exit(main())
