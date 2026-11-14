# The UI

`ui/index.html` is the whole UI: authored at 1280x720 and zoomed to the window,
served by `shell.py` over localhost into a pywebview (WebView2) window.

## Window

- Resizable, held to 16:9 while dragged, minimum 960x540. Maximise and
  fullscreen (F11, Alt+Enter, via `Api.toggle_fullscreen`) take the monitor's
  shape and the page fills it. Escape on the start page also leaves
  fullscreen.
- Opens at 0.55 of the work area, clamped to 1280x720..1600x900 (the authored
  size at 1080p, 1408x792 at 1440p). A screen too small for 1280x720 gets the
  biggest 16:9 that fits, since a 1366x768 laptop has 728px of work area.
- Placed by us, centred on `SPI_GETWORKAREA` (taskbar aware, including one
  docked top or left). pywebview's `CenterScreen` is ignored by the WebView2
  backend ([#1771](https://github.com/r0x0r/pywebview/issues/1771)). Size and
  corner are set on the HWND in real pixels while it's hidden, with chrome
  measured at its own DPI, since pywebview 5 and 6 scale those arguments
  differently.
- The window is created hidden and shown once the page has painted and the
  fonts are ready (`show_when_ready`, 6s deadline), so WebView2's empty first
  frame never shows.

### Holding the ratio

`WM_SIZING` hands over the rect before Windows uses it, so correcting it there
makes the dragged edge follow the mouse at 16:9. The ratio applies to the
client area, chrome measured off the live window, and the edge not being held
stays put. The math is `patcher/display.py`, pure and tested.

On 64-bit Windows the window procedure is a few dozen bytes of x64 from
`patcher/winproc.py`: `WM_SIZING` jumps to the Python callback and everything
else goes to the original procedure through `CallWindowProcW`, with an unwind
entry registered for the frame. A ctypes procedure would run Python, and wait
on the GIL, for every message, including ones WebView2 sends synchronously.
The stub is tested in an x64 emulator and on a real window in Windows CI; if
anything fails to load, the Python procedure is used.

The hook installs after the page paints (a hidden window has nothing to hook)
and the procedure is read back to confirm it took; the `aspect lock:` line
says which outcome happened. If it didn't install, `snap_to_aspect` corrects
the window 250ms after a drag stops, only ever growing, and leaves maximised
and fullscreen windows alone.

### Filling the stage

`fit()` zooms the 1280x720 layout to fit (up to 4x) and gives the stage the
rest of the window's shape: lists stay left, art and drawers right, bars run
across, clips cover. Between 3:2 and 12:5 the stage fills; past that, the
nearest of the two with even bars on whole device pixels, since wider crops
faces out of the clips and 4:3 breaks the start art's fade. The art is capped
at its side's width.

Window size is compositing cost on the fog and cutscene layers. Against a
1440p60 clip, dropped frames were 4.9% at 1280x720, 34.3% at 1920x1080 and
63.8% at 3440x1440.

## Grid: pixel-exact labels

At a fractional zoom every edge and baseline lands between device pixels, and
Blink snaps backgrounds, text and sprites by different rules, so labels drift
up to a pixel off centre at some sizes. `Grid` in `ui/index.html` rounds the
layout after Blink:

- every box with a label puts its face on whole device pixels with a `top`
  offset (a translate would blur edges), at a height whose parity matches the
  label's capitals, measured on a canvas at that size
- the label moves by a whole pixel translate onto the baseline that centres
  its capitals (the trimmed `.label`, never an inline span, whose box runs to
  the descent)
- glyph words go to a whole pixel first, then a translate; icons get a
  translate matched to how an img or svg rounds
- checkbox, tick and the drawn i get whole pixel sizes of matching parity

It runs on resize, on anything built, shown or hidden, on animated height or
margin changes and on scroll, and is idle at rest. Lengths written are on
Blink's 1/64 grid plus 1/256 away from zero, since Blink truncates. Lengths read
are what Blink laid out (typed OM floats truncated the same way); borders come
from `getComputedStyle` with the style checked, because Chromium 153's typed OM
reports `border-style: none` as medium (3px). Half a pixel of hysteresis keeps
a box from flipping between neighbours. PATCH QUEUE is deliberately left where
its CSS puts it.

## Performance

Measured in headless Chromium with software compositing (the case for a
blocklisted GPU, RDP, a VM or Wine).

| Change | Before | After |
|---|---|---|
| nothing animates under the opaque menu 2 layer; fog clip rests there | 548 ms/s CPU during a run, 292 idle | 31, 148 |
| the fog still steps 4x a second | 505 ms/s at 720p | 67 |
| software renderer detected via WebGL (SwiftShader, llvmpipe, WARP) swaps the fog clip for the still | 947 ms/s at 1440p | 224 |
| 720p cuts when the stage is drawn at 1x or under | 665 ms/s over three entries | 350 |
| the glove moves by `translate` | 905 layouts per sweep | 13 |
| HD terminal lines batched, laid out once a frame | 215-238 s XIII-2 install | 116 s (113 with no window) |
| XIII-2 index decrypted in whole-body passes, cached by content | 308 ms scan, 1.2 s survey | 26 ms, 83 ms |
| font preloaded, first art and JSON requested in parallel at script start | ~310 ms to ready | ~230 ms |

Also:

- Entering a game reads every row's width off one layout; transitions flush
  with a computed style read, not `offsetWidth`; the ATB fill animates
  `translateX`.
- The screen change is one style change with every beat a `transition-delay`
  (`--beat` between rows, `--pace` scaling everything), so a slow frame holds
  it all back together. Follow-up work waits on `getAnimations()`, not timers.
- Every run log ends with its five slowest steps.

### Video

- Cuts are fetched one at a time once the window is up (the game on screen
  first) and play from blob URLs, so playback never waits on the Python server.
- Each `<video>` sits at 99% opacity (`--clip-op`). Chromium sends a video
  within 0.001 of opaque to DirectComposition as an underlay, and some drivers
  present that swap chain at a stale size: the clip shrinks into the top left
  over black. `FFXIII_WEBVIEW_ARGS="--autoplay-policy=no-user-gesture-required
  --tint-dc-layer"` tints anything still on a DirectComposition layer.
- `mediaCapabilities` is asked up front whether the GPU decodes 1440p VP9; a no
  starts on the 720p cuts. Cards that decode H.264 but not VP9 (AMD RX 400/500,
  Vega) get the H.264 mp4s.
- Dropped frames are measured once the decoder is warm, and two bad cuts in a
  row step down (mp4 to webm, 1440p to 720p). Never to a still, which is only
  for a clip that can't play. A stalled fog restarts, then rests on the still
  and retries later.
- A 404'd file is dropped for the session; a game with nothing playable gets
  its poster. Failures only touch state while their generation is current.
- Retired layers release their sources, so Chrome's per-tab media pool doesn't
  run dry.

### Media files

The clips are committed, since the release workflow builds the exe from the
checkout. Settle the scanline strength (below) before committing new ones:
every rerun adds a full set of clips to the history. The spec bundles whatever
is in `assets/`, and `check_assets` names any the manifest wants that are
missing.

`cut_<game>_720.webm` beside each cut, encoded from the 4K masters:

```
ffmpeg -i cut_xiii.mp4 -vf scale=1280:720:flags=lanczos -c:v libvpx-vp9 -b:v 0 -crf 30 \
  -row-mt 1 -an -pix_fmt yuv420p -pass 1 -f null /dev/null
ffmpeg -i cut_xiii.mp4 -vf scale=1280:720:flags=lanczos -c:v libvpx-vp9 -b:v 0 -crf 30 \
  -row-mt 1 -an -pix_fmt yuv420p -pass 2 -cpu-used 1 cut_xiii_720.webm
```

The scanlines are XIII's own status-screen pattern (a darker and a lighter
row every 3 rows at 720p). `tools/relines.py` replaces the masters' heavier
lines with it, keeping untouched clips in `.cut-originals/` so reruns start
clean:

```
python tools/relines.py              # 0.7 of the status screen's depth
python tools/relines.py --strength 0.5
python tools/cut_stills.py --force   # regenerate the stills to match
```

Taking baked lines out boosts the rows they darkened, compression noise and
all, so it's always second best. With clips exported without lines, delete
`.cut-originals/`, put each game's clean 4K export in `assets/` as
`cut_<game>.mp4`, and run with `--fresh`: every cut is scaled from that master
with only the new lines added. `.cut-originals/clean` keeps later runs fresh.

`cut_<game>_last.jpg` (and `_first.jpg`) are the stills shown when a clip
can't play, cut by `tools/cut_stills.py`.

## Colour

`.row .lit`'s ramp is the source for structural greys (`--edge`,
`--edge-dim` in `:root`). Gold is state: the ATB fill, a focused select, a
ticked checkbox, a hovered info button, a drop zone with a file over it.
Everything else (drawer rails, headings, idle drop zones, confirm buttons) is
grey. Two tests hold both directions.

## Drawers and input

- Settings and Mods are one drawer at a time (`closeOtherDrawers`); pending
  edits belong to the game and survive switching or closing.
- A drawer belongs to one game, so Q/E is blocked while one is open.
- Close, Escape and a click outside all close it, and Escape doesn't also
  run `back()`.
- The keyboard ring is the patch rows, Apply and Launch, wrapping. The corner
  bar and drawers are pointer only.

## Probes

CI runs `pytest`; anything that must hold is mirrored as an assertion in
`tests/test_engine_coverage.py`. The probes are for iterating.

| Probe | Engine | Checks |
|---|---|---|
| `ui_harness.js` | jsdom | the whole Apply flow behind a fake bridge |
| `ui_probe.js` | Chromium | stage fill and bars at 15 window shapes, corner bar placement, keyboard ring, the screen change frame by frame |
| `center_probe.js` | Chromium | every label, glyph and icon centred in device pixels at 20 sizes and 100/125/150%, plus Grid's rounding rule itself |
| `video_probe.js` | Chromium | cutscene and fog failure handling over real HTTP (generates its own clips with ffmpeg) |
| `settings_probe.js` | jsdom | the drawer rendered from the real `novafix_config` schema |
| `chip_probe.js` | jsdom | every chip state read back off the sprite strip |
| `gauge_probe.js` | jsdom | the ATB fill never decreases during a run |
| `drawer_exit_probe.js` | jsdom | every way out of a drawer |
| `mods_probe.js`, `drop_probe.js`, `extras_probe.js`, `term_probe.js`, `motion_probe.js` | jsdom | the Mods drawer, drops, Worth getting, the HD terminal, motion |

Chromium probes take `CHROME=<path>` to run against a newer build than
Playwright's, which matters since WebView2 updates itself:

```
NODE_PATH=<playwright> node tools/center_probe.js [--quick]
CHROME=<newer chrome> NODE_PATH=<playwright> node tools/ui_probe.js
```

`tools/shot.js` renders the page to png.
