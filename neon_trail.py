"""A neon light-trail that follows the pointer, drawn on a click-through overlay.

A .cur is a fixed bitmap stamped at the hotspot; it cannot draw anything behind
the pointer. A trail has to be painted on the screen, so this puts up a layered
window that ignores the mouse entirely and repaints it as the pointer moves.

Two decisions worth knowing:

  * The window is sized to the TRAIL, not the screen. On a 5120x1440 desktop a
    full-screen layered surface is 29 MB per frame, which at 60 fps is 1.7 GB/s
    of blitting for a few thin lines. Bounding the bitmap to the points plus a
    margin keeps a typical frame around 300x300.
  * WS_EX_TRANSPARENT plus WS_EX_NOACTIVATE means clicks, hovers and focus pass
    straight through: the overlay can never swallow input or steal foreground.

UpdateLayeredWindow wants PREMULTIPLIED BGRA. Feeding it straight alpha gives
bright fringes that look like a rendering fault, so the premultiply at the end
of _bitmap is load-bearing, not tidying.
"""

import ctypes
from ctypes import wintypes

from PIL import Image, ImageDraw

u32 = ctypes.WinDLL("user32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32", use_last_error=True)

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
ULW_ALPHA = 0x00000002
AC_SRC_OVER, AC_SRC_ALPHA = 0x00, 0x01
SWP_NOACTIVATE, SWP_NOSIZE, SWP_NOMOVE = 0x0010, 0x0001, 0x0002
HWND_TOPMOST = -1

MARGIN = 26          # room for the glow to bleed past the end points


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                             ctypes.c_ulonglong, ctypes.c_longlong)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class NeonTrail(object):
    """Owns one overlay window. Cheap to hide, so it is kept between spins."""

    CLASS_NAME = "G9NeonTrail"
    _registered = False

    def __init__(self, gap=7, core=3, glow=11,
                 core_rgb=(238, 248, 255), glow_rgb=(130, 195, 255)):
        # Tron: a near-white silver core inside an ice-blue bloom. A saturated
        # cyan core looked like a highlighter; the light centre is what reads as
        # a light trail rather than a drawn line.
        self.gap = gap              # half the distance between the two rails
        self.core = core            # bright inner line width
        self.glow = glow            # soft outer line width
        self.core_rgb = core_rgb
        self.glow_rgb = glow_rgb
        self.hwnd = None
        self._proc = None           # kept alive: a GC'd WNDPROC crashes Windows

    # ---------------------------------------------------------------- window

    def _register(self):
        if NeonTrail._registered:
            return
        # argtypes must be declared. Without them ctypes guesses, and a 64-bit
        # LPARAM overflows on the way back into DefWindowProcW - every single
        # message to the window raised OverflowError. The window still appeared,
        # so it looked fine while throwing continuously.
        u32.DefWindowProcW.restype = ctypes.c_longlong
        u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                       ctypes.c_ulonglong, ctypes.c_longlong]
        self._proc = WNDPROC(lambda h, m, w, l: u32.DefWindowProcW(h, m, w, l))
        wc = WNDCLASS()
        wc.lpfnWndProc = self._proc
        wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        wc.lpszClassName = self.CLASS_NAME
        if not u32.RegisterClassW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err != 1410:                      # already registered
                raise OSError("RegisterClassW failed: %d" % err)
        NeonTrail._registered = True

    def ensure(self):
        if self.hwnd:
            return self.hwnd
        self._register()
        u32.CreateWindowExW.restype = wintypes.HWND
        self.hwnd = u32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW |
            WS_EX_NOACTIVATE | WS_EX_TOPMOST,
            self.CLASS_NAME, None, WS_POPUP, 0, 0, 1, 1,
            None, None, None, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW failed: %d" % ctypes.get_last_error())
        u32.ShowWindow(self.hwnd, 8)             # SW_SHOWNA, never activate
        return self.hwnd

    def hide(self):
        if self.hwnd:
            u32.ShowWindow(self.hwnd, 0)         # SW_HIDE

    def destroy(self):
        if self.hwnd:
            u32.DestroyWindow(self.hwnd)
            self.hwnd = None

    # ----------------------------------------------------------------- paint

    def _bitmap(self, points, x0, y0, w, h):
        """Two parallel rails along the path, fading toward the tail."""
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img, "RGBA")

        n = len(points)
        for i in range(n - 1):
            ax, ay = points[i][0] - x0, points[i][1] - y0
            bx, by = points[i + 1][0] - x0, points[i + 1][1] - y0
            dx, dy = bx - ax, by - ay
            length = (dx * dx + dy * dy) ** 0.5
            if length < 0.5:
                continue
            # Perpendicular to travel, so the rails stay beside the path
            # whichever way the pointer turns.
            px, py = -dy / length * self.gap, dx / length * self.gap

            fade = (i + 1) / float(n)            # newest segment brightest
            for ox, oy in ((px, py), (-px, -py)):
                d.line([(ax + ox, ay + oy), (bx + ox, by + oy)],
                       fill=self.glow_rgb + (int(70 * fade),),
                       width=self.glow, joint="curve")
                d.line([(ax + ox, ay + oy), (bx + ox, by + oy)],
                       fill=self.core_rgb + (int(235 * fade),),
                       width=self.core, joint="curve")

        # UpdateLayeredWindow expects premultiplied alpha.
        px = img.load()
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a and a != 255:
                    px[x, y] = (r * a // 255, g * a // 255, b * a // 255, a)
        return img

    def update(self, points):
        """points: screen coordinates, oldest first. Fewer than 2 hides it."""
        if len(points) < 2:
            self.hide()
            return

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        x0, y0 = min(xs) - MARGIN, min(ys) - MARGIN
        w = max(xs) - min(xs) + MARGIN * 2
        h = max(ys) - min(ys) + MARGIN * 2
        if w < 2 or h < 2 or w > 4000 or h > 4000:
            self.hide()
            return

        img = self._bitmap(points, x0, y0, w, h)
        hwnd = self.ensure()

        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth, bi.biHeight = w, -h          # top-down
        bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0

        bits = ctypes.c_void_p()
        g32.CreateDIBSection.restype = ctypes.c_void_p
        g32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         wintypes.UINT,
                                         ctypes.POINTER(ctypes.c_void_p),
                                         ctypes.c_void_p, wintypes.DWORD]
        hbm = g32.CreateDIBSection(None, ctypes.byref(bi), 0,
                                   ctypes.byref(bits), None, 0)
        if not hbm:
            return
        g32.CreateCompatibleDC.restype = ctypes.c_void_p
        g32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        mdc = g32.CreateCompatibleDC(None)
        g32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        g32.SelectObject.restype = ctypes.c_void_p
        old = g32.SelectObject(mdc, hbm)

        raw = img.tobytes("raw", "BGRA")
        ctypes.memmove(bits, raw, len(raw))

        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        pos = wintypes.POINT(x0, y0)
        size = wintypes.SIZE(w, h)
        src = wintypes.POINT(0, 0)
        u32.UpdateLayeredWindow.argtypes = [
            wintypes.HWND, ctypes.c_void_p, ctypes.POINTER(wintypes.POINT),
            ctypes.POINTER(wintypes.SIZE), ctypes.c_void_p,
            ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
            ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
        u32.UpdateLayeredWindow(hwnd, None, ctypes.byref(pos),
                                ctypes.byref(size), mdc, ctypes.byref(src),
                                0, ctypes.byref(blend), ULW_ALPHA)
        u32.ShowWindow(hwnd, 8)
        u32.SetWindowPos(hwnd, ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
                         SWP_NOACTIVATE | SWP_NOSIZE | SWP_NOMOVE)

        g32.SelectObject(mdc, old)
        g32.DeleteObject.argtypes = [ctypes.c_void_p]
        g32.DeleteObject(hbm)
        g32.DeleteDC.argtypes = [ctypes.c_void_p]
        g32.DeleteDC(mdc)


def cursor_pos():
    pt = wintypes.POINT()
    u32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y
