"""Prove the neon trail actually reaches the screen, and measure its cost.

An overlay that silently fails to paint looks exactly like one that was never
wired up, so "it renders" is not something to take on trust. This grabs the
screen before and after painting a synthetic path and diffs the two: only pixels
the overlay changed can differ.

It also times a burst of updates, because a trail that paints correctly at 4 fps
is still broken.

    python test_trail.py
"""

import ctypes
import math
import sys
import time
from ctypes import wintypes

from PIL import Image, ImageChops

import neon_trail as NT

u32 = NT.u32
g32 = NT.g32


def grab(x, y, w, h):
    """Screen pixels in that rectangle, right now."""
    screen = u32.GetDC(None)
    g32.CreateCompatibleDC.restype = ctypes.c_void_p
    g32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    mdc = g32.CreateCompatibleDC(ctypes.c_void_p(screen))
    g32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    g32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    hbm = g32.CreateCompatibleBitmap(ctypes.c_void_p(screen), w, h)
    g32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    g32.SelectObject.restype = ctypes.c_void_p
    old = g32.SelectObject(ctypes.c_void_p(mdc), ctypes.c_void_p(hbm))
    g32.BitBlt(ctypes.c_void_p(mdc), 0, 0, w, h,
               ctypes.c_void_p(screen), x, y, 0x00CC0020)   # SRCCOPY

    class BMI(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    bi = BMI()
    bi.biSize = ctypes.sizeof(BMI)
    bi.biWidth, bi.biHeight = w, -h
    bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(w * h * 4)
    g32.GetDIBits(ctypes.c_void_p(mdc), ctypes.c_void_p(hbm), 0, h, buf,
                  ctypes.byref(bi), 0)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")

    g32.SelectObject(ctypes.c_void_p(mdc), ctypes.c_void_p(old))
    g32.DeleteObject.argtypes = [ctypes.c_void_p]
    g32.DeleteObject(ctypes.c_void_p(hbm))
    g32.DeleteDC.argtypes = [ctypes.c_void_p]
    g32.DeleteDC(ctypes.c_void_p(mdc))
    u32.ReleaseDC(None, screen)
    return img


def arc(cx, cy, r, a0, a1, n):
    return [(int(cx + r * math.cos(math.radians(a))),
             int(cy + r * math.sin(math.radians(a))))
            for a in [a0 + (a1 - a0) * i / float(n - 1) for i in range(n)]]


def main():
    checks = []

    # A patch of screen well inside the primary monitor, away from the edges.
    cx, cy, r = 420, 420, 110
    x0, y0, w, h = cx - r - 60, cy - r - 60, 2 * (r + 60), 2 * (r + 60)

    trail = NT.NeonTrail()
    try:
        before = grab(x0, y0, w, h)

        path = arc(cx, cy, r, 200, 340, 14)
        trail.update(path)
        time.sleep(0.45)                    # let the compositor present it
        after = grab(x0, y0, w, h)

        diff = ImageChops.difference(before, after)
        raw = diff.tobytes()
        changed = sum(1 for i in range(0, len(raw), 3)
                      if max(raw[i], raw[i + 1], raw[i + 2]) > 24)
        pct = 100.0 * changed / (w * h)
        checks.append(("trail paints to the screen: %d px changed (%.2f%%)"
                       % (changed, pct), changed > 200))

        # It must be light, not dark: a silver core on an ice bloom.
        if changed:
            px = after.load()
            bp = before.load()
            lit = [(px[i % w, i // w]) for i in range(w * h)
                   if max(abs(px[i % w, i // w][c] - bp[i % w, i // w][c])
                          for c in range(3)) > 24]
            avg = tuple(sum(p[c] for p in lit) // len(lit) for c in range(3))
            checks.append(("painted colour is light and blue-ish: rgb%s" % (avg,),
                           avg[2] >= avg[0] and sum(avg) > 210))

        # Frame rate, on the same bounded bitmap the real thing uses.
        n = 40
        t0 = time.time()
        for i in range(n):
            trail.update(arc(cx, cy, r, 200 + i, 340 + i, 14))
        fps = n / (time.time() - t0)
        checks.append(("updates fast enough: %.0f fps" % fps, fps >= 30))
    finally:
        trail.hide()
        trail.destroy()
        time.sleep(0.2)

    bad = 0
    for label, ok in checks:
        print("  %-56s %s" % (label, "PASS" if ok else "FAIL"))
        bad += 0 if ok else 1
    if bad:
        sys.exit("%d trail check(s) failed" % bad)
    print("neon trail verified against real screen pixels")


if __name__ == "__main__":
    main()
