ReShade 6.8.0 and the Fabula-Lous Shader preset, the shader Extra on all three games.

Bundled under the licences of what's in it, all of which grant redistribution
outright, so there's no ask owed the way there is for a mod. The .ini files and
FabulaLous.fx are this patcher's own.

    d3d9.dll         ReShade32.dll out of the official ReShade_Setup_6.8.0.exe
                     from reshade.me, renamed, never run through the installer.
                     The standard build, not the add-on one. 32-bit, Authenticode
                     signed. crosire, BSD 3-Clause, LICENSE-ReShade.md beside it.
                     size     4,136,216 bytes
                     sha256   538998f66c0197adcdeadfe8e5ea19dd6ca3253e40399476d291f385ce518b88

    reshade-shaders/Shaders/SMAA.fx, LumaSharpen.fx, Curves.fx, Vibrance.fx
                     CeeJay.dk's SweetFX, MIT, LICENSE-SweetFX.txt in
                     reshade-shaders. github.com/CeeJayDK/SweetFX at 93ddf39,
                     unmodified, moved out of its SweetFX subfolders. The
                     preset doesn't run Curves or Vibrance any more; they stay
                     so an install from before has nothing left over that
                     Remove wouldn't take, and they're in ReShade's list for
                     anyone mixing their own.

    reshade-shaders/Shaders/FabulaLous.fx
                     the preset's grade, this patcher's own. Curves' S-curve
                     and Vibrance's rule in one pass, both CeeJay.dk's and
                     credited in its header, with the bright end brought down
                     rather than lifted and vibrance eased off before black or
                     white. FABULA_HAZE, which only XIII-2's preset sets, adds
                     the haze passes below; FABULA_LIFT, LR's, the lift below,
                     in the same one pass. Its defaults are each preset's, so
                     Reset in ReShade's menu lands on the game's own look.

    reshade-shaders/Shaders/SMAA.fxh, Textures/AreaTex.png, Textures/SearchTex.png
                     SMAA by Jorge Jimenez et al, from the same SweetFX commit.
                     Its MIT-style licence is the header of SMAA.fxh.

    reshade-shaders/Shaders/ReShade.fxh, ReShadeUI.fxh
                     github.com/crosire/reshade-shaders, slim branch at
                     fd00221. CC0.

    ReShade.ini      reads everything from this folder, starts on Fabula-Lous Shader,
                     skips the tutorial, Home for the menu, PrintScreen for a
                     shot into Screenshots here. Performance mode, so the preset's
                     values compile in as constants.

    Fabula-Lous Shader.ini
                     SMAA, LumaSharpen, FabulaLous, in that order. Colour edges
                     at SMAA's default threshold, searching 16 steps and 8
                     diagonally (its High preset, which reads the same as
                     Ultra's 32 and 16 at a fraction of the cost on 1440p and
                     4K), sharpening a notch under
                     LumaSharpen's default and clamped as tight, then contrast
                     at 0.15 on luma, Highlights at 0.05 (the bright end down
                     by up to 9.5 levels of 255) and vibrance at 0.12. Nothing
                     reads depth. XIII's.

    Fabula-Lous Shader XIII-2.ini
                     XIII-2's, put in under the same name as the other, so
                     ReShade.ini and the drawer don't tell them apart. The same
                     SMAA and LumaSharpen, then FabulaLous with FABULA_HAZE=1
                     under its own section: Haze 0.45, contrast 0.12,
                     Highlights 0.02, vibrance 0.24. The small pull keeps the
                     S-curve from clipping more of the bright end than the game
                     does: with none, the sunset went from 17% of the frame
                     with a channel at 255 to 32%; with it, 9%.

    Fabula-Lous Shader LR.ini
                     LR's, under the same name again. The same SMAA and
                     LumaSharpen, then FabulaLous with FABULA_LIFT=1: Whites
                     0.05 at most, Blacks 0, Haze 0.15, Clarity 0.25, Glow
                     0.06, contrast 0.14, Highlights 0.02, vibrance 0.10,
                     and the game's own picture kept behind the field HUD
                     at 16:9 (Hud 1.0).

The grade is its own effect because of where FFXIII puts its brightest places.
Orphan's Cradle and the haze over Nautilus Park are drawn near white already,
and Curves' S-curve lifts everything over mid-grey as far as it darkens what's
under it, with Vibrance saturating whatever's bright on top. On the player's
own shot of the Narthex that took the share of the frame with a channel at 255
from an estimated 10% to 15%. The S-curve stays for the shadows; above mid-grey
a bump that peaks at 6/7 and is zero at white brings the bright end down
instead. Vibrance fades out over the brightest third, and eases out over the
last quarter of the way to black or white, so up to 0.25 it never pushes a
channel past either and a dark colour never jumps between no push and a full
one.

XIII-2 lays a grey veil over its sunlit places, the desert at dusk, New Bodhum,
Academia's streets, and its dark rooms have none, so no fixed curve suits both.
With FABULA_HAZE the grade first measures the frame: the darkest channel a
20px patch at a time (16 points across each, half a pixel off so each fetch
blends the pixels around it), those averaged on a 32x18 grid, the rows and
columns black right across from each edge left out (a film's bars, a 16:10
screen's) along with the one a bar's edge cuts through, and a harmonic mean of
the rest, which leans on the darker cells with 0.05 added so one black corner,
the HUD's say, can't decide it alone. Bars have to be black for that, a level
or less: near-black ones, or subtitles in them, count as picture and the veil
comes off less, never more, since a looser line would trim a dark room down to
its one lit window. That level settles toward each frame's over 150ms going
down and 400ms going up, by frame time with a hitch taken as 100ms at most, in
a float 1x1 kept between frames. On a big drop, the frame's level more than
0.08 to 0.16 under the settled one (a cut, a flash ending), what comes off
follows this frame instead, so a dark room after a bright place isn't crushed
while the level comes down; a smaller dip, a dark thing crossing, just settles.
Below 8% of white there's no veil and nothing comes off; it fades in to full by
20%, and Haze of it comes off, a quarter of the range at most. It comes off as
one lift per pixel, off its darkest channel and eased in from black over twice
the veil, so hues stay where they were and nothing under it clips, and the rest
is scaled back up so white stays white. Where only a colour's darkest channel
moves along a smooth ramp, the other two dip a little with it, around ten
levels mid-ramp on synthetic ones and on a few hundredths of a percent of the
shots' pixels: the price of keeping hues.

On the player's eight shots it takes 19 to 37 levels of 255 off the hazy ones
and nothing off the dark room, the shaded alley or the sunlit ruins, and leaves
a channel at 255 on fewer pixels than the game does on all eight. The lift
itself keeps every hue; what turns any is 8-bit rounding of low-colour pixels,
clipping, SMAA and the sharpening. In ReShade 6.8 under wine, frame by frame:
it compiles for D3D9 and matches the numpy model to a level on all eight; the
first dark frame after a cut from the sunset, or after a quarter second of
white, is exactly the settled one; a dark disc over 6% of the frame for one
frame moves the rest by a mean of 0.4 levels, at most 1; dark to hazy comes in
over about a second (a mean of 2 levels off 0.6s in, 1 at 0.85s); and the
sunset letterboxed with black bars of 72, 80 or 92 lines at 720p keeps 36.6 of
its 38.1 levels of veil, where bars counted in took all of it. XIII's preset
draws the same frame as before, byte for byte.

LR runs a clock, from lamplit nights to the Dead Dunes at noon, and its light
is more natural than XIII's or XIII-2's, so FABULA_LIFT reads every frame
before it touches it. A quarter size copy of the frame, already blurred, is
read on a 32x18 grid: each cell's mean luma and darkest channel. From those,
the frame's brightest cell, its mean, and its veil (a harmonic mean of the
darkest channels, XIII-2's way), settled by time: down in 150ms, up in 400.

The top is stretched on luma by however far the brightest cell sits under
0.95, never more than 1 / (1 - Whites), with a tanh shoulder from 0.85 scaled
so white still lands on white. A night street or a dusk sky gets the full
stretch; a frame already up there, the dunes by day, gets none, where a fixed
stretch had taken half of it to pure white. Only the settled read is used:
taking this frame's on the way up flicked the whole frame down whenever a
fast pan slid sky into a cell. A flash the settled read hasn't caught up with
can't clip, since the shoulder lands white on white. Scaling luma keeps each colour's hue, and a
saturated channel the scale takes past white is pulled back toward luma
rather than clipped, so a red lamp stays red.

A lit frame's haze comes off the way XIII-2's does, one lift off each pixel's
darkest channel, bounded the same way on a big drop, and faded in between a
mean luma of 0.3 and 0.42 (the lower of this frame's and the settled), so
night stays night and a cut into a dark room is dark from its first frame. The
shadows can then come down the same way by Blacks, which ships at 0: on the
player's night shots 0.03 tripled the share of the frame under 3% brightness.

Anything the game drew near neutral in the mid tones, white cloth or stone
in shade lit by the sky, keeps the saturation the game gave it: taking a veil
off and pushing vibrance raise a faint blue cast into blue cloth otherwise.
Above 70% brightness it fades out, so a pale sky still goes blue as its haze
comes off.

LR has no HD project behind it, so it also gets what a modern renderer would
give it. Clarity is local contrast: the frame's luma against a blur of itself
at a quarter size, the difference squashed (d / (1 + 4|d|)) so a hard edge like
the HUD or a sign gets far less than its size and nothing halos, and weighted to
the mids. Glow is the frame's light over half brightness, blurred wide at an
eighth size and screened on, so it can take a pixel toward white but never past
it, full in a dark frame and gone by a mean of 0.55, when the light is
everywhere. Every pass draws into a texture a quarter of the frame across at
most, or the fixed 32x18 grid and three single pixels, so together they touch
less than one full frame.

Tuned on the player's sixteen shots of the first day, night, morning and noon,
taken back through src_59's grade to what the game drew and run through this
one in numpy. Dead Dunes at noon goes from 54% of the frame with a channel at
255 to 7%, the Wildlands morning from 21% to 0.3%, and the night shots move by
a level or two. Every pass compiles with Microsoft's d3dcompiler_47 for ps_3_0
as ReShade 6.8 assembles it; not yet seen in game.

Where it goes
-------------
A ReShade folder beside NovaFix, which is a different place in each game:

    XIII             white_data\prog\win\bin\ReShade
    XIII-2           alba_data\prog\win\bin\ReShade
    Lightning Ret.   ReShade, in the game's root beside LRFF13.exe

That's the one way NovaFix's readme says loads it: its D3D9 backend takes
ReShade\d3d9.dll as the renderer on Automatic, after a d3d9_chain.dll and before
the system's, and knows it by its ReShadeVersion export. The pick is the same
code for every title, LR included: NovaFix switches LR's presentation controls
off, but the renderer setting isn't one of them. ReShade 6 reads ReShade.ini
beside its dll and resolves every relative path from that folder, so nothing
here names a path outside it. The three manifests list the same files with the
same pins, but for XIII-2's preset, and a test holds them to it.

ReShade writes ReShade.ini back whenever it exits and saves the preset as it's
tweaked, so both are marked config in the manifests: status goes by the dll and
the shaders, a changed one stays the user's, and ours goes beside it as .new
when it's newer than the one that went in.
It keeps its shader cache in %TEMP%\ReShade, not here. When the game starts it
asks GitHub for ReShade's newest tag, two seconds at most, and its banner says
when there's a newer one. That's ReShade's own and nothing here turns it off.

Replacing it
------------
    1. swap the files in this folder
    2. python tools/pin_bundled.py        (rewrites the sha256 in all three manifests)
    3. pytest -q tests/test_reshade_bundle.py

The tests check the preset against the shaders: every technique it runs is one
an effect declares, every value is a uniform that effect has, in its range. A
SweetFX update that renames one fails there rather than in somebody's game.
