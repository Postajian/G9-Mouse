"""Prove the typed SIZE reaches the pointer, and survives using the mouse.

This is the check that did not exist while the bug did. Everything reported
success: the panel showed the new number, the registry held it, the preview
redrew, and the .cur files really did contain a 128 px image. The only thing
that was wrong was the pointer on screen, and nothing looked at that.

Three ways the size was silently lost, all of them measured on build 26200:

  1. CursorBaseSize + SPI_SETCURSORS does not resize the pointer AT ALL on this
     machine - not for our scheme, and not for stock Windows pointers either.
  2. Every SPI_SETCURSORS broadcast resets all roles to 32, and install() and
     release() both broadcast, so anything that set a size before them lost it.
  3. The click helper loaded its swap artwork with cx/cy of 0, so the first
     press stamped 32 px over whatever size was showing.

So the measurement here is always "what is Windows drawing right now", taken
from GetCursorInfo - never the registry, and never the panel's own idea.

    python test_size.py
"""

import json
import os
import subprocess
import sys
import time

import click_effect as CE
import cursor_cli as CLI
import measure_size as M

# Absolute, because the suite invokes this file from another directory and the
# apply subprocess used to look for "cursor_cli.py" in whatever cwd it inherited
# - so every apply failed and the size never moved, only when run from the
# suite. Standalone it passed, which is exactly how the gap hid.
HERE = os.path.dirname(os.path.abspath(__file__))
CLI_PY = os.path.join(HERE, "cursor_cli.py")

# GetCursorInfo returns the same bitmap regardless, but set awareness anyway so
# nothing downstream reads a DPI-scaled metric.
try:
    M.u32.SetProcessDpiAwarenessContext(M.ctypes.c_void_p(-4))
except (AttributeError, OSError):
    M.u32.SetProcessDPIAware()

# Below 32 the artwork is drawn smaller inside a 32 px cursor instead, so the
# pointer surface stays 32 - that is size_plan's deliberate behaviour, not a
# failure to apply.
SWEEP = [32, 48, 64, 96, 128, 37, 100, 200, 24]


def apply(px):
    r = subprocess.run([sys.executable, CLI_PY, "apply", "--size", str(px)],
                       capture_output=True, text=True)
    lines = [x for x in r.stdout.splitlines() if x.strip()]
    if not lines:
        return False
    try:
        return bool(json.loads(lines[-1]).get("ok"))
    except ValueError:
        return False


def main():
    saved = CLI.load_settings()
    effect_was = bool(CLI.effect_pid())
    if effect_was:
        CLI.stop_effect()
        time.sleep(0.6)

    checks = []
    try:
        for px in SWEEP:
            want = CLI.size_plan(px)[0]
            ok_cli = apply(px)
            time.sleep(0.35)
            got = M.displayed_size()
            checks.append(("size %-4d -> pointer %s" % (px, got),
                           ok_cli and got == (want, want)))

        # Using the mouse must not undo it. press() and release() are the real
        # functions the helper runs, called directly so nothing is stubbed.
        apply(96)
        time.sleep(0.35)
        checks.append(("still 96 after apply", M.displayed_size() == (96, 96)))

        pressed = CE.load_pressed_set()
        pressed = pressed[0] if isinstance(pressed, tuple) else pressed
        CE.press(pressed)
        time.sleep(0.3)
        checks.append(("still 96 while a button is held",
                       M.displayed_size() == (96, 96)))

        CE.release()
        time.sleep(0.45)
        checks.append(("still 96 after letting go", M.displayed_size() == (96, 96)))

        # The spinner swaps every role to one .ani; it must keep the size too.
        cfg = {k: v["value"] for k, v in CE.I.read_current().items()}
        resting = cfg.get("Arrow", "")
        if resting:
            CE.set_all(CE.spin_path_for(resting, 1), CE.OCR.values())
            time.sleep(0.3)
            checks.append(("still 96 while spinning",
                           M.displayed_size() == (96, 96)))
            CE.release()
            time.sleep(0.4)
    finally:
        apply(saved["size"])
        if effect_was:
            CLI.start_effect()

    bad = 0
    for label, ok in checks:
        print("  %-42s %s" % (label, "PASS" if ok else "FAIL"))
        bad += 0 if ok else 1
    if bad:
        sys.exit("%d size check(s) failed" % bad)
    print("the typed size reaches the pointer and survives clicking")


if __name__ == "__main__":
    main()
