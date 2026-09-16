"""Install / restore a G9 cursor set under HKCU\\Control Panel\\Cursors.

Everything here is per-user and reversible. The first thing an install does is
write a restore point of whatever is currently configured, so the old pointers
can always be put back - by this script, or by hand in Mouse Properties.

    python install_cursors.py reticle      install variant A, all 15 roles
    python install_cursors.py octagram     install variant B (8 star), all 15
    python install_cursors.py pick reticle/No octagram/UpArrow ...
                                           install only the named roles and
                                           leave every other role stock
    python install_cursors.py original     back to the pointers you had BEFORE
                                           this tool was ever run
    python install_cursors.py stock        plain Windows pointers
    python install_cursors.py restore      undo just the last change
    python install_cursors.py show         print what is configured right now
"""

import ctypes
from ctypes import wintypes
import datetime
import json
import os
import sys
import winreg

HERE = os.path.dirname(os.path.abspath(__file__))
CUR = os.path.join(HERE, "cursors")
BACKUP = os.path.join(HERE, "restore_point")
ORIGINAL = os.path.join(BACKUP, "original.json")   # written once, never overwritten

KEY = r"Control Panel\Cursors"

# The 15 registry value names Windows reads, and the file each one maps to.
ROLES = ["Arrow", "Help", "AppStarting", "Wait", "Crosshair", "IBeam", "NWPen",
         "No", "SizeNS", "SizeWE", "SizeNWSE", "SizeNESW", "SizeAll", "UpArrow",
         "Hand"]
ANIMATED = {"Wait", "AppStarting"}

SPI_SETCURSORS = 0x0057
SPIF_UPDATEINI = 0x01
SPIF_SENDCHANGE = 0x02


# One name per set, in one place. A two-way "8 Star or else Reticle" meant
# every set added later registered itself under the Reticle name, so Mouse
# Properties showed one scheme silently overwriting another.
SCHEME_NAMES = {"octagram": "8 Star", "reticle": "Reticle", "arrow": "Arrow"}


def scheme_name(variant):
    return SCHEME_NAMES.get(variant, variant.replace("_", " ").title())


def read_current():
    """Every value under the Cursors key, plus the scheme name."""
    out = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
        i = 0
        while True:
            try:
                name, val, typ = winreg.EnumValue(k, i)
            except OSError:
                break
            out[name] = {"value": val, "type": typ}
            i += 1
    return out


def apply_now():
    """Make Windows reload the pointers from the registry.

    SPIF_SENDCHANGE on its own. Adding SPIF_UPDATEINI makes this particular
    action return 0 with GetLastError 0 - measured on Windows 11 26200 - and
    it is not wanted anyway, since the registry is already written by hand.
    """
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.SystemParametersInfoW.restype = wintypes.BOOL
    u32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT,
                                          ctypes.c_void_p, wintypes.UINT]
    ctypes.set_last_error(0)
    if not u32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, SPIF_SENDCHANGE):
        raise OSError("SystemParametersInfo(SPI_SETCURSORS) failed, "
                      "GetLastError=%d" % ctypes.get_last_error())


def seed_original(current):
    """Pin the pointers as they were BEFORE this tool ever touched them.

    Written once and never again. The per-apply restore points are useless for
    getting back to normal: every apply writes one, so after the second apply
    the newest 'restore point' is itself a G9 cursor set. And blanking to
    Windows stock is not right either - this machine already had its own
    scheme (arrow_eoa.cur and friends) before any of this, and that is what
    'my normal mouse' means here.
    """
    if os.path.isfile(ORIGINAL):
        return
    os.makedirs(BACKUP, exist_ok=True)
    if any(("G9 Cursor" in str(d["value"])) for d in current.values()):
        return          # already ours; refuse to record that as the original
    with open(ORIGINAL, "w", encoding="utf-8") as fh:
        json.dump(current, fh, indent=2)


def prune_restore_points(keep=20):
    """Auto-apply writes one per change, so these would grow without limit."""
    points = sorted(f for f in os.listdir(BACKUP)
                    if f.startswith("cursors_") and f.endswith(".json"))
    for name in points[:-keep] if len(points) > keep else []:
        for ext in (".json", ".reg"):
            try:
                os.remove(os.path.join(BACKUP, name[:-5] + ext))
            except OSError:
                pass


def save_restore_point():
    os.makedirs(BACKUP, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    current = read_current()
    seed_original(current)

    js = os.path.join(BACKUP, "cursors_%s.json" % stamp)
    with open(js, "w", encoding="utf-8") as fh:
        json.dump(current, fh, indent=2)

    # A .reg as well, so the old pointers can be restored by double-click even
    # if Python is not around.
    reg = os.path.join(BACKUP, "cursors_%s.reg" % stamp)
    with open(reg, "w", encoding="utf-16") as fh:
        fh.write("Windows Registry Editor Version 5.00\r\n\r\n")
        fh.write("[HKEY_CURRENT_USER\\Control Panel\\Cursors]\r\n")
        for name, d in sorted(current.items()):
            key = '@' if name == "" else '"%s"' % name
            if d["type"] == winreg.REG_DWORD:
                fh.write('%s=dword:%08x\r\n' % (key, d["value"]))
            else:
                fh.write('%s="%s"\r\n' % (key, str(d["value"]).replace("\\", "\\\\")))

    with open(os.path.join(BACKUP, "latest.txt"), "w", encoding="utf-8") as fh:
        fh.write(js)
    prune_restore_points()
    return js, reg


def install(variant):
    src = os.path.join(CUR, variant, "resting")
    if not os.path.isdir(src):
        sys.exit("no such variant: %s (looked in %s)" % (variant, src))

    # A role whose artwork has been deleted is left stock rather than aborting
    # the whole install. That is what makes it safe to remove individual roles
    # you do not want: the rest of the set still installs.
    paths = {}
    for role in ROLES:
        ext = ".ani" if role in ANIMATED else ".cur"
        p = os.path.join(src, role + ext)
        if os.path.isfile(p):
            paths[role] = p
    if not paths:
        sys.exit("no cursor files left in " + src)

    js, reg = save_restore_point()
    print("restore point -> %s" % js)
    print("              -> %s" % reg)

    name = "G9 %s" % scheme_name(variant)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        for role in ROLES:
            # Blank, not skipped: a role left over from a previous install would
            # otherwise keep pointing at artwork this variant no longer has.
            winreg.SetValueEx(k, role, 0, winreg.REG_SZ, paths.get(role, ""))
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, name)
        winreg.SetValueEx(k, "Scheme Source", 0, winreg.REG_DWORD, 2)

    # Register it as a named scheme so it also shows in the Mouse Properties
    # dropdown and survives being switched away from and back.
    order = [paths.get(r, "") for r in ROLES]
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY + r"\Schemes") as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_EXPAND_SZ, ",".join(order))

    apply_now()
    print("installed scheme: %s" % name)
    return name


def pick(pairs):
    """Install a hand-picked subset. Roles left out are reset to stock Windows.

    An empty REG_SZ under the Cursors key is exactly how the stock 'Windows
    Default' scheme marks a role, so dropped roles are blanked rather than
    deleted - that is what makes them fall back cleanly.
    """
    chosen = {}
    for pair in pairs:
        if "/" not in pair:
            sys.exit("expected variant/Role, got: " + pair)
        variant, role = pair.split("/", 1)
        if role not in ROLES:
            sys.exit("unknown role: " + role)
        if role in chosen:
            print("! %s is already taken by %s/%s - ignoring %s"
                  % (role, chosen[role][0], role, pair))
            continue
        ext = ".ani" if role in ANIMATED else ".cur"
        p = os.path.join(CUR, variant, "resting", role + ext)
        if not os.path.isfile(p):
            sys.exit("missing cursor file: " + p)
        chosen[role] = (variant, p)

    js, reg = save_restore_point()
    print("restore point -> %s" % js)
    print("              -> %s" % reg)

    name = "G9 Custom"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        for role in ROLES:
            val = chosen[role][1] if role in chosen else ""
            winreg.SetValueEx(k, role, 0, winreg.REG_SZ, val)
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, name)
        winreg.SetValueEx(k, "Scheme Source", 0, winreg.REG_DWORD, 2)

    order = [chosen[r][1] if r in chosen else "" for r in ROLES]
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY + r"\Schemes") as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_EXPAND_SZ, ",".join(order))
        # also park both complete sets in the dropdown, so a full set is one
        # click away in Mouse Properties without re-running this script
        for variant, label in (("reticle", "G9 Reticle"), ("octagram", "G9 8 Star")):
            full = []
            for r in ROLES:
                e = ".ani" if r in ANIMATED else ".cur"
                full.append(os.path.join(CUR, variant, "resting", r + e))
            winreg.SetValueEx(k, label, 0, winreg.REG_EXPAND_SZ, ",".join(full))

    apply_now()
    print("installed scheme: %s  (%d custom, %d stock)"
          % (name, len(chosen), len(ROLES) - len(chosen)))
    for role in ROLES:
        if role in chosen:
            print("   %-12s %s" % (role, chosen[role][0]))
    return name


def apply_values(data):
    """Write exactly this set of values, removing anything not in it."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0,
                        winreg.KEY_SET_VALUE | winreg.KEY_READ) as k:
        for name in list(read_current()):
            if name not in data:
                try:
                    winreg.DeleteValue(k, name)
                except OSError:
                    pass
        for name, d in data.items():
            winreg.SetValueEx(k, name, 0, d["type"], d["value"])
    apply_now()


def restore_original():
    """Back to the pointers from before this tool was ever run."""
    if not os.path.isfile(ORIGINAL):
        return windows_stock()
    apply_values(json.load(open(ORIGINAL, encoding="utf-8")))
    return "original"


def windows_stock():
    """Plain Windows pointers. An empty value per role is how the stock
    'Windows Default' scheme itself marks them, so this is what Mouse
    Properties would do, not an imitation of it."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        for role in ROLES:
            winreg.SetValueEx(k, role, 0, winreg.REG_SZ, "")
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "Windows Default")
        winreg.SetValueEx(k, "Scheme Source", 0, winreg.REG_DWORD, 0)
        winreg.SetValueEx(k, "CursorBaseSize", 0, winreg.REG_DWORD, 32)
    apply_now()
    return "windows"


def restore():
    latest = os.path.join(BACKUP, "latest.txt")
    if not os.path.isfile(latest):
        sys.exit("no restore point found in " + BACKUP)
    js = open(latest, encoding="utf-8").read().strip()
    data = json.load(open(js, encoding="utf-8"))

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0,
                        winreg.KEY_SET_VALUE | winreg.KEY_READ) as k:
        for name in list(read_current()):
            if name not in data:
                try:
                    winreg.DeleteValue(k, name)
                except OSError:
                    pass
        for name, d in data.items():
            winreg.SetValueEx(k, name, 0, d["type"], d["value"])

    apply_now()
    print("restored from %s" % js)


def show():
    for name, d in sorted(read_current().items()):
        print("  %-14s %s" % (name or "(default)", d["value"]))


if __name__ == "__main__":
    arg = (sys.argv[1] if len(sys.argv) > 1 else "show").lower()
    if arg in ("reticle", "octagram"):
        install(arg)
        show()
    elif arg == "pick":
        if len(sys.argv) < 3:
            sys.exit("pick needs at least one variant/Role")
        pick(sys.argv[2:])
    elif arg == "restore":
        restore()
    elif arg == "original":
        print("restored:", restore_original())
        show()
    elif arg == "stock":
        print("restored:", windows_stock())
        show()
    elif arg == "show":
        show()
    else:
        sys.exit(__doc__)
