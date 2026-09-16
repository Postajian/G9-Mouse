# G9 Mouse

Custom Windows mouse cursors, generated from geometry rather than drawn by hand,
with a live click/hold/spin effect layer on top.

No SVG rasteriser, no ImageMagick, no Inkscape. Shapes are plain polygons drawn
at 4x supersample and Lanczos-downsampled, and the `.cur` / `.ani` containers are
written byte by byte. The only dependency is Pillow.

## The two cursor sets

**8 Star** (Rub el Hizb) - two squares at 0 and 45 degrees, one stroke each.
Thin outline while resting; bold interlaced bands that weave over and under at
all eight crossings while a button is held.

**Reticle** - four corner brackets, two ginger and two cyan on opposite
diagonals. Pressing reverses the diagonals and closes the frame inward.

Both ship all 15 Windows cursor roles at 32/48/64/96/128 in a single `.cur`, so
the Windows cursor-size slider stays sharp. The frame is the family signature;
only the centre mark changes per role.

## Live effects

With the helper running, the mouse itself drives the pointer:

| Input | Result |
|---|---|
| Hold one button | The pressed artwork |
| Both buttons | Latches into a spin. Let go and it keeps turning; the next click stops it. Steps up a gear every 2 s through 5 gears |
| Hold LEFT, tap RIGHT twice | Switch to the next cursor set |
| Triple-tap numpad `+` | Open the control panel at the pointer |

## Things Windows does not let you do, and what was done instead

**There is no "pressed" cursor role.** Windows never changes the pointer on
mouse-down. The helper swaps the live cursors with `SetSystemCursor` while a
button is down, and restores with one `SystemParametersInfo(SPI_SETCURSORS)`.

**The button is polled, not hooked.** Windows silently evicts a `WH_MOUSE_LL`
hook that answers slowly and tells you nothing, which is how hook-based tools
die without a trace. A poll cannot be evicted.

**`CopyImage` flattens a multi-resolution cursor.** Duplicating a handle for
`SetSystemCursor` collapsed it to one image, so the swapped-in pointer came out
visibly smaller. Measured 10.3% of pixels wrong versus 4.1% when loaded fresh.
Every press loads a fresh handle.

**Cursors cannot be smaller than 32 px.** `CursorBaseSize` below 32 is silently
clamped - measured, not assumed. Sizes under 32 are delivered by drawing a
smaller mark inside a 32 px cursor.

**Smoothness is frames per second, speed is degrees per frame.** The gears first
shared one frame count and varied playback rate, which made gear 1 play at
7.5 fps and stutter. Each gear now has its own frame count at a constant 30 fps.

## Files

```
build_cursors.py     the geometry and the .cur/.ani writers
install_cursors.py   registry install, restore points, and going back
click_effect.py      the press / spin / gesture helper
cursor_cli.py        one JSON seam, used by the G9 PC Control panel
verify_cursors.py    re-parses every file and hands it to Windows to load
prove_installed.py   asks Windows what it is USING and diffs it against disk
test_latch.py        drives the real helper loop with scripted buttons
make_picker.py       renders the selectable role grid
neon_trail.py        click-through overlay for a pointer trail
```

## Build and install

```
python build_cursors.py            # draw every set
python verify_cursors.py           # parse-check, and make Windows load each one
python install_cursors.py octagram # install the 8 Star
python install_cursors.py original # back to the pointers you had before
```

Everything is per-user under `HKCU\Control Panel\Cursors` and reversible. The
first install pins the pre-existing pointers into `restore_point/original.json`
and never overwrites that file, because the per-change restore points are useless
for getting back to normal - after the second change the newest one is itself a
generated set.

## Verification

The interesting checks do not trust the registry. `prove_installed.py` calls
`LoadCursorW` with the standard `IDC_*` ids to get what Windows currently has
loaded, draws that handle, and diffs it against the file on disk. A correct
cursor measures 2-7% of pixels different from resampling; a genuinely wrong one
measured 22-31%.

## Requires

Windows, Python 3, Pillow.
