"""Ask Windows what cursor it is actually using, and compare it to our files.

Reading the registry back only proves the registry was written. LoadCursorW with
a standard IDC_* id returns the cursor the system has LOADED for that role right
now, so drawing that handle and diffing it against our own .cur is end-to-end
proof that the pointer on screen is ours.

Roles the user left stock are expected to NOT match - that is reported as the
correct result, not as a failure.
"""

import ctypes
import os
import sys
from ctypes import wintypes

from PIL import Image, ImageChops, ImageDraw, ImageFont

import install_cursors as I
import verify_cursors as V

HERE = os.path.dirname(os.path.abspath(__file__))
GRAY = (128, 128, 128, 255)
N = 32

# How different the live cursor may be from its file and still count as a match.
#
# Chosen from measurements, not taste. A correct cursor measures 2.3 - 3.2% for
# the normal frame and 7.0% for Hand, whose tighter star packs far more edge
# pixels into the same area so antialiasing differences weigh more. A genuinely
# WRONG cursor - the pressed artwork showing while resting was expected -
# measured 22 - 31%. Twelve sits in the empty gap between those two clusters.
MATCH_MAX = 12.0

# Role -> IDC_* id. NWPen has no IDC constant, so it can only be checked in the
# registry; every other role can be proven through the live system handle.
IDC = {"Arrow": 32512, "IBeam": 32513, "Wait": 32514, "Crosshair": 32515,
       "UpArrow": 32516, "SizeNWSE": 32642, "SizeNESW": 32643, "SizeWE": 32644,
       "SizeNS": 32645, "SizeAll": 32646, "No": 32648, "Hand": 32649,
       "AppStarting": 32650, "Help": 32651}

u32 = ctypes.WinDLL("user32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32", use_last_error=True)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def draw_system_cursor(role):
    """Render whatever Windows currently has loaded for this role."""
    u32.LoadCursorW.restype = ctypes.c_void_p
    u32.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    hcur = u32.LoadCursorW(None, ctypes.c_void_p(IDC[role]))
    if not hcur:
        return None

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = N
    bi.biHeight = -N              # negative: top-down, so rows need no flipping
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0

    bits = ctypes.c_void_p()
    g32.CreateDIBSection.restype = ctypes.c_void_p
    g32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
                                     ctypes.POINTER(ctypes.c_void_p),
                                     ctypes.c_void_p, wintypes.DWORD]
    hbm = g32.CreateDIBSection(None, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
    if not hbm:
        return None

    g32.CreateCompatibleDC.restype = ctypes.c_void_p
    g32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    hdc = g32.CreateCompatibleDC(None)
    g32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    g32.SelectObject.restype = ctypes.c_void_p
    old = g32.SelectObject(hdc, hbm)

    # opaque grey ground, so an alpha-blended cursor actually shows up
    buf = (ctypes.c_ubyte * (N * N * 4)).from_address(bits.value)
    for i in range(0, N * N * 4, 4):
        buf[i], buf[i + 1], buf[i + 2], buf[i + 3] = 128, 128, 128, 255

    u32.DrawIconEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                               ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                               wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    u32.DrawIconEx(hdc, 0, 0, hcur, N, N, 0, None, 0x0003)   # DI_NORMAL

    img = Image.frombuffer("RGBA", (N, N), bytes(buf), "raw", "BGRA", 0, 1).copy()

    g32.SelectObject(hdc, old)
    g32.DeleteObject.argtypes = [ctypes.c_void_p]
    g32.DeleteObject(hbm)
    g32.DeleteDC.argtypes = [ctypes.c_void_p]
    g32.DeleteDC(hdc)
    return img


def ours_on_grey(path):
    img = V.images_of(path)[N]
    base = Image.new("RGBA", (N, N), GRAY)
    base.alpha_composite(img)
    return base


def diff(a, b):
    """Percent of pixels that differ STRUCTURALLY, not just in resampling.

    Windows scales the cursor from whichever size in the .cur best fits the
    system cursor-size setting, so its 32px bitmap has slightly different edge
    antialiasing than our own Lanczos downsample. A mean-absolute-difference
    therefore never reaches zero even on a perfect match. Counting only pixels
    whose worst channel is off by more than 48 ignores edge halos and still
    catches a genuinely different shape.
    """
    d = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    raw = d.tobytes()
    n = len(raw) // 3
    hits = sum(1 for i in range(0, len(raw), 3)
               if max(raw[i], raw[i + 1], raw[i + 2]) > 48)
    return 100.0 * hits / n


def main():
    cfg = {k: v["value"] for k, v in I.read_current().items()}
    print("active scheme: %s\n" % (cfg.get("", "(none)") or "(none)"))

    rows, fails = [], 0
    for role in I.ROLES:
        want = cfg.get(role, "")
        custom = bool(want)
        if role not in IDC:
            print("%-12s %-8s registry only (no IDC id): %s"
                  % (role, "CUSTOM" if custom else "stock",
                     os.path.basename(want) if want else "-"))
            rows.append((role, None, None, custom))
            continue

        live = draw_system_cursor(role)
        if live is None:
            print("%-12s LoadCursorW failed" % role)
            fails += 1
            continue

        if custom:
            d = diff(live, ours_on_grey(want))
            ok = d < MATCH_MAX
            fails += 0 if ok else 1
            print("%-12s CUSTOM   differs on %5.1f%% of pixels   %s"
                  % (role, d, "MATCH" if ok else "MISMATCH"))
        else:
            d = None
            print("%-12s stock    (left as Windows default, as requested)" % role)
        rows.append((role, live, d, custom))

    # contact sheet of what the system is really handing out
    try:
        f = ImageFont.truetype(V.TIMES, 13)
        fs = ImageFont.truetype(V.TIMES, 11)
    except OSError:
        f = fs = ImageFont.load_default()
    cols, cw, ch = 8, 116, 104
    shown = [r for r in rows if r[1] is not None]
    sheet = Image.new("RGB", (cols * cw, ((len(shown) + cols - 1) // cols) * ch + 30),
                      (22, 22, 22))
    d = ImageDraw.Draw(sheet)
    d.text((cols * cw // 2, 6), "WHAT WINDOWS IS ACTUALLY USING RIGHT NOW",
           font=f, anchor="ma", fill=(212, 175, 55))
    for i, (role, live, _dd, custom) in enumerate(shown):
        x, y = (i % cols) * cw, 28 + (i // cols) * ch
        big = live.resize((64, 64), Image.NEAREST).convert("RGB")
        sheet.paste(big, (x + cw // 2 - 32, y + 4))
        d.rectangle([x + cw // 2 - 33, y + 3, x + cw // 2 + 32, y + 68],
                    outline=(90, 90, 90))
        d.text((x + cw // 2, y + 72), role, font=fs, anchor="ma",
               fill=(235, 235, 235))
        d.text((x + cw // 2, y + 86), "CUSTOM" if custom else "stock", font=fs,
               anchor="ma", fill=(120, 230, 160) if custom else (140, 140, 140))
    out = os.path.join(HERE, "proof_live_cursors.png")
    sheet.save(out)
    print("\nproof sheet -> %s" % out)
    if fails:
        sys.exit("%d role(s) did not match" % fails)
    print("every custom role matches the file on disk")


if __name__ == "__main__":
    main()
