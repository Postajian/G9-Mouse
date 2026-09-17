"""Ask Windows how big the pointer it has LOADED actually is.

The registry value and the panel both report what was asked for. This reports
what Windows ended up with, which is the only number that matches what the user
sees on screen.

    python measure_size.py
"""

import ctypes
import winreg
from ctypes import wintypes

import install_cursors as I

u32 = ctypes.WinDLL("user32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32", use_last_error=True)

# Declared, not guessed. A GDI handle read out of a struct is a full 64-bit
# value, and ctypes' default int marshalling overflows on it.
g32.DeleteObject.argtypes = [ctypes.c_void_p]
g32.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
u32.GetIconInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p]

IDC = {"Arrow": 32512, "IBeam": 32513, "Wait": 32514, "Crosshair": 32515,
       "UpArrow": 32516, "SizeNWSE": 32642, "SizeNESW": 32643, "SizeWE": 32644,
       "SizeNS": 32645, "SizeAll": 32646, "No": 32648, "Hand": 32649,
       "AppStarting": 32650, "Help": 32651}


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                ("hbmColor", wintypes.HBITMAP)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG),
                ("bmHeight", wintypes.LONG), ("bmWidthBytes", wintypes.LONG),
                ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", ctypes.c_void_p)]


def loaded_size(role="Arrow"):
    """Pixel size of the cursor Windows currently has for this role.

    Measured off the bitmap inside the cursor handle, not off any file: this is
    the same object the compositor draws, so it cannot disagree with the screen.
    A monochrome cursor has no colour bitmap and stacks AND over XOR in one mask,
    which is why the height is halved in that case."""
    u32.LoadCursorW.restype = wintypes.HANDLE
    h = u32.LoadCursorW(None, ctypes.c_void_p(IDC[role]))
    if not h:
        return None
    info = ICONINFO()
    if not u32.GetIconInfo(h, ctypes.byref(info)):
        return None
    bm = BITMAP()
    src = info.hbmColor or info.hbmMask
    g32.GetObjectW(ctypes.c_void_p(src), ctypes.sizeof(BITMAP), ctypes.byref(bm))
    w, hgt = bm.bmWidth, bm.bmHeight
    if not info.hbmColor:
        hgt //= 2
    for b in (info.hbmColor, info.hbmMask):
        if b:
            g32.DeleteObject(ctypes.c_void_p(b))
    return w, hgt


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]


def displayed_size():
    """Size of the cursor Windows is drawing RIGHT NOW.

    This is the honest instrument. LoadCursorW and SM_CXCURSOR both go through
    the legacy 32 px path - SM_CXCURSOR is documented as a fixed metric, and
    measuring either of them reported 32 no matter what CursorBaseSize said,
    which looked like "the size never applies" whether or not it had.
    GetCursorInfo hands back the handle actually on screen."""
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    if not u32.GetCursorInfo(ctypes.byref(ci)) or not ci.hCursor:
        return None
    info = ICONINFO()
    if not u32.GetIconInfo(ci.hCursor, ctypes.byref(info)):
        return None
    bm = BITMAP()
    src = info.hbmColor or info.hbmMask
    g32.GetObjectW(ctypes.c_void_p(src), ctypes.sizeof(BITMAP), ctypes.byref(bm))
    w, hgt = bm.bmWidth, bm.bmHeight
    if not info.hbmColor:
        hgt //= 2
    for b in (info.hbmColor, info.hbmMask):
        if b:
            g32.DeleteObject(ctypes.c_void_p(b))
    return w, hgt


def base_size():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, I.KEY) as k:
            return int(winreg.QueryValueEx(k, "CursorBaseSize")[0])
    except OSError:
        return None


def system_metric():
    return u32.GetSystemMetrics(13), u32.GetSystemMetrics(14)   # SM_CXCURSOR/CY


if __name__ == "__main__":
    # Per-monitor aware, or every metric here comes back scaled for 96 DPI.
    try:
        u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        u32.SetProcessDPIAware()
    print("CursorBaseSize in registry : %s" % base_size())
    print("cursor ON SCREEN right now : %s" % (displayed_size(),))
    print("SM_CXCURSOR / SM_CYCURSOR  : %s" % (system_metric(),))
    for role in ("Arrow", "Hand", "IBeam", "Help"):
        print("loaded %-12s          : %s" % (role, loaded_size(role)))
