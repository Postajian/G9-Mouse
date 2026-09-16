"""Check every generated cursor, then render previews at true size.

Three gates, weakest to strongest:
  1. re-parse each .cur / .ani with a reader that shares no code with the writer
  2. hand every file to Windows itself via LoadCursorFromFileW - the only proof
     that counts, because a malformed DIB fails silently in Explorer
  3. render previews at TRUE 32 px on light and dark, since that is the size the
     cursor is actually used at and the only honest way to judge the shape
"""

import ctypes
import glob
import os
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
CUR = os.path.join(HERE, "cursors")
TIMES = r"C:\Windows\Fonts\times.ttf"

VARIANTS = ["reticle", "octagram"]
STATES = ["resting", "pressed"]
ROLES = ["Arrow", "Hand", "IBeam", "Crosshair", "No", "Help", "UpArrow",
         "SizeNS", "SizeWE", "SizeNWSE", "SizeNESW", "SizeAll", "NWPen"]

GOLD = (212, 175, 55)
BG = (22, 22, 22)


def parse_cur(data):
    """[(w, h, hotspot_x, hotspot_y, RGBA image), ...] from .cur bytes."""
    res, typ, count = struct.unpack_from("<HHH", data, 0)
    assert res == 0 and typ == 2, "not a cursor: reserved=%d type=%d" % (res, typ)
    out = []
    for i in range(count):
        w, h, _, _, hx, hy, _n, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        w, h = w or 256, h or 256
        _sz, bw, bh, _pl, bpp, _cp = struct.unpack_from("<IiiHHI", data, off)
        assert bh == bw * 2, "DIB height %d is not 2x width %d" % (bh, bw)
        assert bpp == 32, "expected 32bpp, got %d" % bpp
        img = Image.new("RGBA", (bw, bw))
        ld = img.load()
        px = off + 40
        for y in range(bw - 1, -1, -1):
            for x in range(bw):
                b, g, r, a = data[px:px + 4]
                px += 4
                ld[x, y] = (r, g, b, a)
        out.append((w, h, hx, hy, img))
    return out


def parse_ani(data):
    """The embedded .cur blobs of an .ani, in frame order."""
    assert data[:4] == b"RIFF" and data[8:12] == b"ACON", "not an ACON riff"
    frames, pos, end = [], 12, struct.unpack_from("<I", data, 4)[0] + 8
    while pos < end:
        tag = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + size]
        if tag == b"LIST" and body[:4] == b"fram":
            p = 4
            while p < len(body):
                t2 = body[p:p + 4]
                s2 = struct.unpack_from("<I", body, p + 4)[0]
                if t2 == b"icon":
                    frames.append(body[p + 8:p + 8 + s2])
                p += 8 + s2 + (s2 % 2)
        pos += 8 + size + (size % 2)
    return frames


def windows_loads(path):
    """Gate 2. A zero handle means Windows refused the file."""
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.LoadCursorFromFileW.restype = ctypes.c_void_p
    u32.LoadCursorFromFileW.argtypes = [ctypes.c_wchar_p]
    h = u32.LoadCursorFromFileW(path)
    if not h:
        return False, ctypes.get_last_error()
    u32.DestroyCursor.argtypes = [ctypes.c_void_p]
    u32.DestroyCursor(ctypes.c_void_p(h))
    return True, 0


def images_of(path):
    data = open(path, "rb").read()
    if path.endswith(".ani"):
        data = parse_ani(data)[0]
    return {im[0]: im[4] for im in parse_cur(data)}


def swatch(sheet, img32, x, y):
    """True 32 px, shown once on light and once on dark."""
    d = ImageDraw.Draw(sheet)
    d.rectangle([x, y, x + 40, y + 40], fill=(242, 242, 242))
    d.rectangle([x + 40, y, x + 80, y + 40], fill=(10, 10, 10))
    sheet.paste(img32, (x + 4, y + 4), img32)
    sheet.paste(img32, (x + 44, y + 4), img32)
    d.rectangle([x, y, x + 80, y + 40], outline=(80, 80, 80))


def fonts():
    try:
        return (ImageFont.truetype(TIMES, 22), ImageFont.truetype(TIMES, 16),
                ImageFont.truetype(TIMES, 13))
    except OSError:
        f = ImageFont.load_default()
        return f, f, f


def sheet_pick():
    """The head-to-head sheet: 2 cursors, resting vs pressed."""
    big, mid, small = fonts()
    cw, ch = 470, 250
    sheet = Image.new("RGB", (cw * 2, ch + 44), BG)
    d = ImageDraw.Draw(sheet)
    d.text((cw, 12), "TWO CURSORS  .  RESTING  VS  CLICKED", font=big,
           anchor="ma", fill=GOLD)

    titles = {"reticle": "A .  RETICLE", "octagram": "B .  8 STAR  (RUB EL HIZB)"}
    for col, variant in enumerate(VARIANTS):
        ox = col * cw
        d.rectangle([ox + 8, 44, ox + cw - 9, ch + 36], outline=(70, 70, 70))
        d.text((ox + cw // 2, 52), titles[variant], font=mid, anchor="ma", fill=GOLD)

        for row, state in enumerate(STATES):
            x = ox + 24 + row * 222
            imgs = images_of(os.path.join(CUR, variant, state, "Arrow.cur"))
            d.text((x + 100, 80), state.upper(), font=mid, anchor="ma",
                   fill=(235, 235, 235))
            shown = imgs[max(imgs)].resize((128, 128), Image.LANCZOS)
            sheet.paste(shown, (x + 36, 104), shown)
            swatch(sheet, imgs[32], x + 60, 240)
            d.text((x + 100, 284), "true 32px", font=small, anchor="ma",
                   fill=(150, 150, 150))
    out = os.path.join(HERE, "preview_pick.png")
    sheet.save(out)
    return out


def sheet_roles():
    """All 15 roles for both variants, resting state."""
    _big, mid, small = fonts()
    cols, cw, ch = 8, 150, 164
    names = ROLES + ["Wait", "AppStarting"]
    rows = (len(names) + cols - 1) // cols
    block = rows * ch + 40
    sheet = Image.new("RGB", (cols * cw, block * 2 + 12), BG)
    d = ImageDraw.Draw(sheet)

    for vi, variant in enumerate(VARIANTS):
        top = vi * block + 6
        d.text((cols * cw // 2, top + 4), "%s  .  all 15 roles  .  resting"
               % variant.upper(), font=mid, anchor="ma", fill=GOLD)
        for i, name in enumerate(names):
            ext = ".ani" if name in ("Wait", "AppStarting") else ".cur"
            path = os.path.join(CUR, variant, "resting", name + ext)
            if not os.path.isfile(path):
                # Roles can be deleted deliberately. A gap is a gap, not a
                # failure, so it is labelled rather than crashed on.
                cx, cy = (i % cols) * cw, top + 30 + (i // cols) * ch
                d.rectangle([cx + 4, cy + 4, cx + cw - 5, cy + ch - 8],
                            outline=(50, 50, 50))
                d.text((cx + cw // 2, cy + ch // 2 - 16), "removed", font=small,
                       anchor="ma", fill=(120, 90, 90))
                d.text((cx + cw // 2, cy + ch - 24), name, font=small,
                       anchor="ma", fill=(110, 110, 110))
                continue
            imgs = images_of(path)
            cx, cy = (i % cols) * cw, top + 30 + (i // cols) * ch
            d.rectangle([cx + 4, cy + 4, cx + cw - 5, cy + ch - 8],
                        outline=(62, 62, 62))
            shown = imgs[max(imgs)].resize((72, 72), Image.LANCZOS)
            sheet.paste(shown, (cx + cw // 2 - 36, cy + 12), shown)
            swatch(sheet, imgs[32], cx + cw // 2 - 40, cy + 90)
            d.text((cx + cw // 2, cy + ch - 24), name, font=small, anchor="ma",
                   fill=(225, 225, 225))
    out = os.path.join(HERE, "preview_roles.png")
    sheet.save(out)
    return out


def main():
    files = sorted(glob.glob(os.path.join(CUR, "*", "*", "*.cur")) +
                   glob.glob(os.path.join(CUR, "*", "*", "*.ani")))
    if not files:
        sys.exit("no cursors found - run build_cursors.py first")

    bad = []
    for path in files:
        rel = os.path.relpath(path, CUR)
        data = open(path, "rb").read()
        try:
            if path.endswith(".ani"):
                frames = parse_ani(data)
                assert frames, "no frames"
                parse_cur(frames[0])
            else:
                for w, _h, hx, hy, _im in parse_cur(data):
                    assert hx == w // 2 and hy == w // 2, \
                        "hotspot %d,%d not centred for %dpx" % (hx, hy, w)
        except AssertionError as e:
            bad.append("parse   %s: %s" % (rel, e))
            continue
        ok, err = windows_loads(path)
        if not ok:
            bad.append("winload %s: GetLastError=%d" % (rel, err))

    print("%d files checked" % len(files))
    for line in bad:
        print("FAIL " + line)
    if bad:
        sys.exit("\n%d file(s) failed" % len(bad))
    print("all pass: parse + centred hotspot + Windows LoadCursorFromFileW")
    print("preview -> " + sheet_pick())
    print("preview -> " + sheet_roles())


if __name__ == "__main__":
    main()
