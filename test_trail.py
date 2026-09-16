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

        # ---------------------------------------------------------- the knobs
        # A settings row that reads back correctly but never reaches the screen
        # is the whole failure mode here, so each knob is measured in pixels.
        trail.hide()
        time.sleep(0.25)

        def lit(cfg):
            """Paint one arc with this tail and count the pixels it changed."""
            t = NT.from_settings(cfg)
            base = grab(x0, y0, w, h)
            t.update(arc(cx, cy, r, 200, 340, 14))
            time.sleep(0.35)
            shot = grab(x0, y0, w, h)
            t.hide(); t.destroy()
            time.sleep(0.2)
            raw = ImageChops.difference(base, shot).tobytes()
            px = [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]
            on = [p for p in px if max(p) > 24]
            avg = tuple(sum(p[c] for p in on) // max(1, len(on)) for c in range(3))
            # Total light ADDED, not the average of the lit pixels. Turning the
            # contrast up widens the faint outer glow, which pulls the average
            # DOWN even as the tail visibly burns brighter - the average is the
            # wrong instrument for this knob.
            energy = sum(sum(p) for p in px)
            return len(on), avg, energy

        thin, _a, _e = lit({"core": 1, "glow": 2, "gap": 3, "contrast": 100,
                       "coreColour": "#eef8ff", "glowColour": "#82c3ff"})
        thick, _a, _e = lit({"core": 9, "glow": 22, "gap": 14, "contrast": 100,
                        "coreColour": "#eef8ff", "glowColour": "#82c3ff"})
        checks.append(("thickness is real: %d px thin vs %d px thick"
                       % (thin, thick), thick > thin * 1.6))

        _n, _a, dim_energy = lit({"core": 5, "glow": 14, "gap": 7, "contrast": 20,
                            "coreColour": "#eef8ff", "glowColour": "#82c3ff"})
        _n, _a, loud_energy = lit({"core": 5, "glow": 14, "gap": 7, "contrast": 200,
                              "coreColour": "#eef8ff", "glowColour": "#82c3ff"})
        checks.append(("contrast is real: %.0fk light at 20%% vs %.0fk at 200%%"
                       % (dim_energy / 1000.0, loud_energy / 1000.0),
                       loud_energy > dim_energy * 1.3))

        _n, red, _e = lit({"core": 7, "glow": 16, "gap": 7, "contrast": 140,
                       "coreColour": "#ff2020", "glowColour": "#ff6060"})
        checks.append(("colour is real: a red tail paints rgb%s" % (red,),
                       red[0] > red[2] + 20))

        trail = NT.NeonTrail()

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

    # ------------------------------------------------------------ live reload
    # The panel writes settings and does NOT restart the helper, so a typed
    # change that only lands on the next launch is the same as no change.
    import os
    import subprocess
    import click_effect as CE
    saved = open(CE.SETTINGS, encoding="utf-8").read()
    try:
        before_cfg = dict(CE.trail_settings())
        subprocess.run([sys.executable, "cursor_cli.py", "trail",
                        "--ms", "900", "--core", "8", "--contrast", "175"],
                       capture_output=True, text=True, check=True)
        os.utime(CE.SETTINGS, None)
        after_cfg = CE.trail_settings()
        checks.append(("helper follows a typed change with no restart: "
                       "%d -> %d ms" % (before_cfg["ms"], after_cfg["ms"]),
                       after_cfg["ms"] == 900 and after_cfg["core"] == 8
                       and after_cfg["contrast"] == 175))
    finally:
        open(CE.SETTINGS, "w", encoding="utf-8").write(saved)

    bad = 0
    for label, ok in checks:
        print("  %-56s %s" % (label, "PASS" if ok else "FAIL"))
        bad += 0 if ok else 1
    if bad:
        sys.exit("%d trail check(s) failed" % bad)
    print("neon trail verified against real screen pixels")


if __name__ == "__main__":
    main()
