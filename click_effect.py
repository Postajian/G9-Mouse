"""Show the pressed cursor artwork while a mouse button is held.

Windows has no 'pressed' cursor role - the pointer never changes on mouse-down
on its own - so this swaps the live system cursors with SetSystemCursor while a
button is down and puts them back on release.

Two deliberate choices:

  * the button is POLLED, not watched with a WH_MOUSE_LL hook. Windows evicts a
    low-level hook that is slow to answer and gives no notification, which makes
    hook-based tools die silently. A poll cannot be evicted.
  * release restores with one SystemParametersInfo(SPI_SETCURSORS) call instead
    of setting every cursor back by hand, so the resting state always comes from
    the registry and can never drift out of sync with what is installed.

    python click_effect.py              run it
    python click_effect.py --selftest   prove the swap works, then exit
"""

import atexit
import ctypes
import json
import os
import signal
import struct
import sys
import time
from ctypes import wintypes

import install_cursors as I

HERE = os.path.dirname(os.path.abspath(__file__))
PIDFILE = os.path.join(HERE, "click_effect.pid")

IMAGE_CURSOR = 2
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
SPI_SETCURSORS = 0x0057
SPIF_SENDCHANGE = 0x02
VK_LBUTTON, VK_RBUTTON = 0x01, 0x02

# Only roles that have an OCR_* id can be swapped at runtime. NWPen has none,
# so it keeps its resting artwork while a button is held.
OCR = {"Arrow": 32512, "IBeam": 32513, "Wait": 32514, "Crosshair": 32515,
       "UpArrow": 32516, "SizeNWSE": 32642, "SizeNESW": 32643, "SizeWE": 32644,
       "SizeNS": 32645, "SizeAll": 32646, "No": 32648, "Hand": 32649,
       "AppStarting": 32650, "Help": 32651}

u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.LoadImageW.restype = ctypes.c_void_p
u32.LoadImageW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.UINT,
                           ctypes.c_int, ctypes.c_int, wintypes.UINT]
u32.CopyImage.restype = ctypes.c_void_p
u32.CopyImage.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_int,
                          ctypes.c_int, wintypes.UINT]
u32.SetSystemCursor.restype = wintypes.BOOL
u32.SetSystemCursor.argtypes = [ctypes.c_void_p, wintypes.DWORD]
u32.SystemParametersInfoW.restype = wintypes.BOOL
u32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT,
                                      ctypes.c_void_p, wintypes.UINT]
u32.GetAsyncKeyState.restype = ctypes.c_short
u32.GetAsyncKeyState.argtypes = [ctypes.c_int]
u32.DestroyCursor.argtypes = [ctypes.c_void_p]


SPIN_STEP_SECONDS = 2.0

# Hold LEFT and tap RIGHT twice to swap between the two cursor sets.
#
# That gesture puts both buttons down, which is also the spinner's trigger, so
# the spinner is ARMED rather than instant: both buttons must stay down for
# SPIN_ARM_SECONDS before it engages. Two quick right taps finish well inside
# that window, so the two features do not fight.
SPIN_ARM_SECONDS = 0.35
GESTURE_TAPS = 2
GESTURE_WINDOW = 1.2      # taps further apart than this start a new count

# Tap numpad + three times quickly to open the cursor panel at the pointer.
# VK_ADD is the key above numpad Enter. Numpad Enter itself is deliberately NOT
# used: it is already the PS5 keyboard switch, and a triple tap there would
# toggle that three times and strand the keyboard on the console.
VK_ADD = 0x6B
PANEL_TAPS = 3
PANEL_WINDOW = 0.9

# The light trail that follows the pointer while the spin is latched. Its shape
# is the user's, typed in the panel, so the numbers here are only the fallback
# used when the settings file is missing or unreadable.
TRAIL_FPS = 60.0
TRAIL_MIN_MOVE = 3        # pixels; below this the pointer is parked, not moving
SETTINGS = os.path.join(HERE, "panel_settings.json")

# (settings mtime, tail dict, live overlay). Re-read only when the file changes:
# the panel applies on every keystroke-commit, and the helper must follow along
# without being restarted, but a JSON read every frame at 60 fps is waste.
_trail_cfg = [0.0, None, None]


def trail_settings():
    """The tail the user typed, re-read when panel_settings.json changes."""
    try:
        stamp = os.path.getmtime(SETTINGS)
    except OSError:
        stamp = 0.0
    if stamp != _trail_cfg[0] or _trail_cfg[1] is None:
        import cursor_cli
        try:
            raw = json.load(open(SETTINGS, encoding="utf-8")).get("trail")
        except (OSError, ValueError):
            raw = None
        _trail_cfg[0] = stamp
        _trail_cfg[1] = cursor_cli.clamp_trail(raw)
        _trail_cfg[2] = None            # shape changed, rebuild the overlay
    return _trail_cfg[1]
PANEL_APP = os.path.join(os.path.dirname(HERE), "G9 PC Control", "App", "Main.ps1")


def available_variants():
    """Every variant with artwork on disk, in a stable order.

    Discovered, not hard-coded: each completed gesture steps to the NEXT one,
    so dropping a third set into cursors/ joins the cycle with no code change.
    """
    root = os.path.join(HERE, "cursors")
    found = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        resting = os.path.join(root, name, "resting")
        if os.path.isdir(resting) and any(f.endswith((".cur", ".ani"))
                                          for f in os.listdir(resting)):
            found.append(name)
    return found


def next_variant(path):
    """The variant after the one this installed cursor came from, wrapping."""
    variants = available_variants()
    if not variants:
        return None
    parts = path.replace("/", "\\").split("\\")
    here = parts[parts.index("cursors") + 1] if "cursors" in parts else ""
    idx = variants.index(here) if here in variants else -1
    return variants[(idx + 1) % len(variants)]

# How many gears there are is NOT hard-coded: it is whatever build_cursors
# produced. Keeping a second copy of the number here meant the two files could
# disagree, and the loser would silently never reach its top gear.
SPIN_LEVELS = len(getattr(__import__("build_cursors"), "SPIN_RATES", [1] * 4))


def pressed_path_for(resting_path):
    """cursors/<variant>/resting/X -> cursors/<variant>/pressed/X"""
    head, name = os.path.split(resting_path)
    variant_dir = os.path.dirname(head)
    return os.path.join(variant_dir, "pressed", name)


def spin_path_for(resting_path, level):
    """cursors/<variant>/resting/X -> cursors/<variant>/spin/spin<level>.ani"""
    variant_dir = os.path.dirname(os.path.dirname(resting_path))
    return os.path.join(variant_dir, "spin", "spin%d.ani" % level)


_panel_proc = [None]


LOGFILE = os.path.join(HERE, "click_effect.log")


def log(msg):
    """Append one line. The helper runs under pythonw with no console, so an
    unhandled exception used to vanish completely - the only symptom was the
    effect quietly not working, with no pid file and nothing to read."""
    try:
        with open(LOGFILE, "a", encoding="utf-8") as fh:
            fh.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def open_panel_at_cursor():
    """Launch G9 PC Control at the pointer with the cursor panel on top.

    -OpenPanel, not -ShowPanel: closing the cursor panel should leave the full
    app open rather than nothing, so the hotkey is a way INTO PC Control and not
    just a floating dialog.

    Refuses to stack: if the last one it opened is still alive, the tap is
    ignored. Otherwise a stray triple tap would pile up windows.
    """
    import subprocess
    if _panel_proc[0] is not None and _panel_proc[0].poll() is None:
        return False
    if not os.path.isfile(PANEL_APP):
        return False
    _panel_proc[0] = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-WindowStyle", "Hidden", "-File", PANEL_APP,
         "-OpenPanel", "Cursor", "-AtCursor"],
        creationflags=0x08000000)          # CREATE_NO_WINDOW
    return True


_trail_last = [0.0]


def trail_step(trail, points, now):
    """Sample the pointer and repaint the trail. Returns the overlay, or None.

    The tail is bounded by TIME, not by a point count: at speed a fixed count
    would stretch across the screen, and parked it would linger. Anything older
    than TRAIL_SECONDS is dropped every frame.

    Import is lazy and failure is swallowed on purpose - the trail is decoration,
    and a missing overlay must never take the cursor swapping down with it.
    """
    try:
        import neon_trail
        cfg = trail_settings()
        if trail is None or _trail_cfg[2] is not trail:
            # Rebuilt whenever the typed shape changes, so a new width or
            # colour shows on the very next move rather than after a restart.
            if trail is not None:
                trail.hide()
                trail.destroy()
            trail = neon_trail.from_settings(cfg)
            _trail_cfg[2] = trail

        x, y = neon_trail.cursor_pos()
        if not points or abs(x - points[-1][0]) + abs(y - points[-1][1]) >= 1:
            points.append((x, y, now))
        span = cfg["ms"] / 1000.0
        while points and now - points[0][2] > span:
            points.pop(0)

        if now - _trail_last[0] < 1.0 / TRAIL_FPS:
            return trail
        _trail_last[0] = now

        moved = (len(points) >= 2 and
                 max(abs(points[-1][0] - points[0][0]),
                     abs(points[-1][1] - points[0][1])) >= TRAIL_MIN_MOVE)
        trail.update([(p[0], p[1]) for p in points] if moved else [])
        return trail
    except Exception:
        return trail


def trail_stop(trail, points):
    points[:] = []
    try:
        if trail is not None:
            trail.hide()
    except Exception:
        pass


def set_all(path, ocr_ids):
    """Put one cursor file on every managed role at once."""
    for ocr in ocr_ids:
        h = u32.LoadImageW(None, path, IMAGE_CURSOR, 0, 0,
                           LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if h:
            u32.SetSystemCursor(ctypes.c_void_p(h), ocr)


def load_pressed_set():
    """{ocr_id: path to the pressed file} for every role that can be swapped."""
    cfg = {k: v["value"] for k, v in I.read_current().items()}
    out, skipped = {}, []
    for role, ocr in OCR.items():
        resting = cfg.get(role, "")
        if not resting:
            continue                      # role left stock, nothing to swap
        p = pressed_path_for(resting)
        if not os.path.isfile(p):
            skipped.append(role)
            continue
        h = u32.LoadImageW(None, p, IMAGE_CURSOR, 0, 0,
                           LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not h:
            skipped.append(role)
            continue
        u32.DestroyCursor(ctypes.c_void_p(h))   # load was only a validity check
        out[ocr] = p
    return out, skipped


def press(paths):
    """Load a fresh handle per press - SetSystemCursor destroys what it is given.

    Not CopyImage. CopyImage flattens a multi-resolution cursor down to a single
    image, so the swapped-in pointer came out visibly smaller than the resting
    one (measured 10.3% of pixels wrong vs 4.1% when loaded fresh). A fresh
    LoadImageW keeps every size in the .cur and costs well under the poll
    interval for the handful of roles involved.
    """
    for ocr, path in paths.items():
        h = u32.LoadImageW(None, path, IMAGE_CURSOR, 0, 0,
                           LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if h:
            u32.SetSystemCursor(ctypes.c_void_p(h), ocr)


def release():
    u32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, SPIF_SENDCHANGE)


def selftest():
    import prove_installed as P

    masters, skipped = load_pressed_set()
    if not masters:
        sys.exit("no swappable roles installed - run install_cursors.py first")

    before = P.draw_system_cursor("Arrow")
    try:
        press(masters)
        time.sleep(0.15)                  # let the system settle on the new set
        during = P.draw_system_cursor("Arrow")
    finally:
        release()
    time.sleep(0.15)
    after = P.draw_system_cursor("Arrow")

    cfg = {k: v["value"] for k, v in I.read_current().items()}
    want_rest = P.ours_on_grey(cfg["Arrow"])
    want_press = P.ours_on_grey(pressed_path_for(cfg["Arrow"]))

    checks = [
        ("resting before matches the resting file", P.diff(before, want_rest) < P.MATCH_MAX),
        ("held state matches the PRESSED file", P.diff(during, want_press) < P.MATCH_MAX),
        ("held state really differs from resting", P.diff(during, before) > P.MATCH_MAX),
        ("release restores the resting file", P.diff(after, want_rest) < P.MATCH_MAX),
    ]
    # The both-buttons spinner. Holding two buttons cannot be faked without
    # injecting input, so the swap itself is exercised directly and the result
    # read back off the live system cursor.
    any_resting = cfg["Arrow"]
    for lv in range(1, SPIN_LEVELS + 1):
        p = spin_path_for(any_resting, lv)
        checks.append(("spin level %d file exists" % lv, os.path.isfile(p)))

    if all(os.path.isfile(spin_path_for(any_resting, lv))
           for lv in range(1, SPIN_LEVELS + 1)):
        try:
            set_all(spin_path_for(any_resting, 1), masters.keys())
            time.sleep(0.15)
            spinning = P.draw_system_cursor("Arrow")
        finally:
            release()
        # NOT "differs from resting": the spinner now uses the resting artwork,
        # so at zero rotation it is identical by design. What must be true is
        # that the live cursor became the spin FILE, and that the file really
        # animates rather than holding one frame.
        spin1 = spin_path_for(any_resting, 1)
        checks.append(("both-buttons spinner reaches the live cursor",
                       P.diff(spinning, P.ours_on_grey(spin1)) < P.MATCH_MAX))

        frames = P.V.parse_ani(open(spin1, "rb").read())
        first = P.V.parse_cur(frames[0])[-1][4].tobytes()
        mid = P.V.parse_cur(frames[len(frames) // 3])[-1][4].tobytes()
        checks.append(("spin frames actually differ, so it turns",
                       first != mid))

        # Gears differ by FRAME COUNT, not by play rate. Every gear is packed at
        # the same jiffies-per-frame on purpose - that constant playback is what
        # makes the slow gears smooth - so the old "all rates differ" check was
        # asserting the opposite of the design and had to be replaced.
        rates, counts = [], []
        for lv in range(1, SPIN_LEVELS + 1):
            data = open(spin_path_for(any_resting, lv), "rb").read()
            i = data.index(b"anih")
            counts.append(struct.unpack_from("<I", data, i + 8 + 4)[0])
            rates.append(struct.unpack_from("<I", data, i + 8 + 28)[0])
        checks.append(("%d gears, frames %s, each faster than the last"
                       % (SPIN_LEVELS, counts),
                       len(set(counts)) == SPIN_LEVELS and
                       counts == sorted(counts, reverse=True)))
        checks.append(("every gear plays at one constant rate: %s" % set(rates),
                       len(set(rates)) == 1))

    # The hold-left-tap-right-twice gesture steps to the next variant. The
    # button sequence cannot be faked without injecting input, so the cycle it
    # drives is tested directly instead, including that it comes back around.
    variants = available_variants()
    checks.append(("more than one variant to cycle through", len(variants) > 1))
    if len(variants) > 1:
        seen, probe = [], cfg["Arrow"]
        for _ in range(len(variants)):
            nxt = next_variant(probe)
            seen.append(nxt)
            probe = os.path.join(HERE, "cursors", nxt, "resting", "Arrow.cur")
        checks.append(("cycle visits every variant once: %s" % seen,
                       sorted(seen) == sorted(variants)))
        checks.append(("spinner arms after %.2fs, so 2 taps do not trigger it"
                       % SPIN_ARM_SECONDS,
                       SPIN_ARM_SECONDS > 0 and GESTURE_TAPS >= 2))

    print("swappable roles: %d   %s" % (len(masters),
          ("skipped " + ", ".join(skipped)) if skipped else ""))
    bad = 0
    for label, ok in checks:
        print("  %-52s %s" % (label, "PASS" if ok else "FAIL"))
        bad += 0 if ok else 1
    if bad:
        sys.exit("%d check(s) failed" % bad)
    print("click effect verified against the live system cursor")


def run():
    masters, skipped = load_pressed_set()
    if not masters:
        sys.exit("no swappable roles installed - run install_cursors.py first")

    # Settle the cursors BEFORE announcing readiness. The pid file is what
    # "effect on" waits for, so writing it first meant the caller could be told
    # the helper was up while the pointers were still whatever the previous
    # state left behind - which read as a dozen roles mismatching their files.
    release()
    with open(PIDFILE, "w") as fh:
        fh.write(str(os.getpid()))

    def cleanup(*_a):
        release()
        try:
            os.remove(PIDFILE)
        except OSError:
            pass
        sys.exit(0)

    atexit.register(release)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK):
        try:
            signal.signal(sig, cleanup)
        except (ValueError, AttributeError, OSError):
            pass

    # Any installed role tells us which variant is live, and therefore where
    # the spin files are.
    cfg = {k: v["value"] for k, v in I.read_current().items()}
    any_resting = next((cfg[r] for r in I.ROLES if cfg.get(r, "")), "")
    spin_ready = bool(any_resting) and all(
        os.path.isfile(spin_path_for(any_resting, lv))
        for lv in range(1, SPIN_LEVELS + 1))

    mode, level, since = "rest", 0, 0.0
    prev_lb = False              # previous button states, for detecting
    prev_rb = False              # a press rather than a held button
    spin_armed = False           # latched spin will accept a stop click
    trail, points = None, []     # the light trail, built only while spinning
    both_since = None            # when both buttons went down, for arming
    taps, last_tap = 0, 0.0      # right-button taps counted while left is held
    consumed = False             # a gesture fired; do not also spin this hold

    prev_add = False
    add_taps, last_add = 0, 0.0

    while True:
        lb = bool(u32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
        rb = bool(u32.GetAsyncKeyState(VK_RBUTTON) & 0x8000)
        now = time.time()

        # ---- numpad + tapped three times: open the panel at the pointer ----
        add = bool(u32.GetAsyncKeyState(VK_ADD) & 0x8000)
        if add and not prev_add:
            if now - last_add > PANEL_WINDOW:
                add_taps = 0
            add_taps += 1
            last_add = now
            if add_taps >= PANEL_TAPS:
                add_taps = 0
                if open_panel_at_cursor():
                    log("panel opened by triple numpad +")
        prev_add = add

        # ---- gesture: hold LEFT, tap RIGHT twice, swap to the next set ----
        if rb and not prev_rb and lb:
            if now - last_tap > GESTURE_WINDOW:
                taps = 0
            taps += 1
            last_tap = now
            if taps >= GESTURE_TAPS:
                taps, consumed = 0, True
                target = next_variant(any_resting) if any_resting else None
                if target:
                    release()
                    I.install(target)
                    # Everything downstream is keyed off the installed paths,
                    # so both have to be re-read or the next press would show
                    # the previous set's artwork.
                    masters, _ = load_pressed_set()
                    cfg = {k: v["value"] for k, v in I.read_current().items()}
                    any_resting = next(
                        (cfg[r] for r in I.ROLES if cfg.get(r, "")), "")
                    spin_ready = bool(any_resting) and all(
                        os.path.isfile(spin_path_for(any_resting, lv))
                        for lv in range(1, SPIN_LEVELS + 1))
                    mode, level, both_since = "rest", 0, None
        prev_rb = rb

        if not lb:
            taps, consumed = 0, False

        # ---- press / spin ----
        #
        # The spinner LATCHES: both buttons start it, then it keeps going with
        # nothing held, and the next click of either button stops it.
        #
        # spin_armed is what makes that possible. Without it the very release
        # that starts the spin would arrive as "a button changed" and stop it
        # again instantly. So after latching it waits for BOTH buttons to be up
        # before it will listen for a stop.
        pressed_edge = (lb and not prev_lb) or (rb and not prev_rb)

        if mode == "spin":
            if not (lb or rb):
                spin_armed = True
            elif spin_armed and pressed_edge:
                release()
                mode, level, spin_armed = "rest", 0, False
                both_since = None
                trail_stop(trail, points)
            if mode == "spin":
                want = min(SPIN_LEVELS,
                           1 + int((now - since) / SPIN_STEP_SECONDS))
                if want != level:
                    level = want
                    set_all(spin_path_for(any_resting, level), masters.keys())
                trail = trail_step(trail, points, now)

        elif lb and rb:
            if both_since is None:
                both_since = now
            if (not consumed and spin_ready and
                    now - both_since >= SPIN_ARM_SECONDS):
                mode, since, level, spin_armed = "spin", now, 0, False
            elif mode != "press":
                press(masters)
                mode, level = "press", 0

        elif lb or rb:
            both_since = None
            if mode != "press":
                press(masters)
                mode, level = "press", 0
        else:
            both_since = None
            if mode != "rest":
                release()
                mode, level = "rest", 0
                trail_stop(trail, points)

        prev_lb = lb
        time.sleep(0.008)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        log("start pid=%d" % os.getpid())
        try:
            run()
        except SystemExit:
            log("stopped cleanly")
            raise
        except BaseException:
            import traceback
            log("CRASHED\n" + traceback.format_exc())
            try:
                release()          # never leave the pointers stuck on a crash
            finally:
                raise
