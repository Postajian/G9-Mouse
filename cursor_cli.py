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


SIZE_MIN, SIZE_MAX = 12, 256


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
