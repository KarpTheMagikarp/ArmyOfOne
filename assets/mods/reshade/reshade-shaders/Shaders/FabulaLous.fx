// Fabula-Lous Shader's grade: contrast and vibrance in one pass, tuned for FFXIII.
// The game draws its brightest places near white already (Orphan's Cradle, the haze
// over Nautilus Park), and a plain S-curve lifts that end further still. This keeps
// the S-curve under mid-grey, brings the bright end down a touch instead, and eases
// vibrance off before it'd push a channel past white or black. The S-curve and the
// vibrance rule are CeeJay.dk's, from SweetFX's Curves and Vibrance (MIT,
// LICENSE-SweetFX.txt).
//
// XIII-2's preset sets FABULA_HAZE as well. XIII-2 lays a grey veil over its sunlit
// places (the desert at dusk, New Bodhum, Academia's streets) that a fixed curve
// can't take off without crushing its dark rooms, which have none, so the frame's
// darkest level is measured every frame and only a veil that's there comes off.
//
// LR's preset sets FABULA_LIFT. LR runs a clock, and its frames go from night lit by
// lamps to a desert at noon, so nothing about its grade is fixed: each frame is read
// off a quarter size copy of itself. A frame whose brightest region sits under white
// (night, dusk, a lamplit street) has its top stretched up to it with a soft shoulder;
// one already there (Dead Dunes by day) isn't pushed past it. A lit frame's haze comes
// off the way XIII-2's does, and night keeps the look LR gave it. Then what a modern
// renderer gives a frame and LR's doesn't: local contrast, so stone and cloth read as
// having depth, and a soft glow off the light in a dark frame. All of it comes off a
// quarter and an eighth size copy of the frame, a fraction of a millisecond at 4K.

#ifndef FABULA_HAZE
	#define FABULA_HAZE 0
#endif
#ifndef FABULA_LIFT
	#define FABULA_LIFT 0
#endif

// each game's look is its preset's, so Reset in ReShade's menu lands on it
#if FABULA_HAZE
	#define FABULA_CONTRAST 0.12
	#define FABULA_HIGHLIGHTS 0.02
	#define FABULA_VIBRANCE 0.24
#else
	#if FABULA_LIFT
		#define FABULA_CONTRAST 0.14
		#define FABULA_HIGHLIGHTS 0.02
		#define FABULA_VIBRANCE 0.10
	#else
		#define FABULA_CONTRAST 0.15
		#define FABULA_HIGHLIGHTS 0.05
		#define FABULA_VIBRANCE 0.12
	#endif
#endif

#include "ReShadeUI.fxh"

uniform float Contrast < __UNIFORM_SLIDER_FLOAT1
	ui_min = 0.0; ui_max = 0.5;
	ui_tooltip = "The S-curve on luma, the smoothstep SweetFX's Curves runs: deeper shadows, steeper mids.";
> = FABULA_CONTRAST;

uniform float Highlights < __UNIFORM_SLIDER_FLOAT1
	ui_min = 0.0; ui_max = 0.1;
	ui_tooltip = "How much comes off the bright end at most, around 86% brightness, on top of the S-curve. White stays white.";
> = FABULA_HIGHLIGHTS;

uniform float Vibrance < __UNIFORM_SLIDER_FLOAT1
	ui_min = 0.0; ui_max = 0.25;
	ui_tooltip = "Saturates dull colours more than vivid ones, easing off toward white and black and never pushing past either.";
> = FABULA_VIBRANCE;

#if FABULA_LIFT
uniform float Whites < __UNIFORM_SLIDER_FLOAT1
	ui_min = 0.0; ui_max = 0.25;
	ui_tooltip = "The furthest under white a frame's brightest region can sit and still be stretched up to it. Read every frame, so a bright day already at white isn't pushed past it. A soft shoulder from 85% keeps a sky's gradient. Kept small so a lamp at night stays a lamp and the shadows around it stay shadows.";
> = 0.05;

uniform float Blacks < __UNIFORM_SLIDER_FLOAT1
	ui_min = 0.0; ui_max = 0.08;
	ui_tooltip = "How far the shadows come down toward black, eased in so nothing under them crushes. Off by default: LR's night and shade are already as dark as they read well.";
> = 0.0;

uniform float Clarity < __UNIFORM_SLIDER_FLOAT1
	ui_min = 0.0; ui_max = 1.0;
	ui_tooltip = "Local contrast: the detail against a blur of the frame around it, strongest in the mids. Hard edges are held back so nothing halos.";
> = 0.25;

uniform float Glow < __UNIFORM_SLIDER_FLOAT1
	ui_min = 0.0; ui_max = 0.4;
	ui_tooltip = "A soft glow off the brightest light, screened on so it never clips anything that wasn't already white. Full at night, gone by day, when the light is everywhere. Kept low so lamps don't bloom over the dark around them.";
> = 0.06;

uniform float Hud < __UNIFORM_SLIDER_FLOAT1
	ui_min = 0.0; ui_max = 1.0;
	ui_label = "HUD backdrop";
	ui_tooltip = "How much of the game's own picture is kept behind the field HUD, the gauge bottom left and the map top right, so it reads as clearly as the game drew it. Only at 16:9, where LR puts them.";
> = 1.0;

// the frame at a quarter, blurred there, and its bright part at an eighth. Each
// blur's halves ping-pong through the Soft texture, so four textures hold six passes
texture FabulaQuarterTex { Width = BUFFER_WIDTH / 4; Height = BUFFER_HEIGHT / 4; Format = RGBA16F; };
texture FabulaSoftTex { Width = BUFFER_WIDTH / 4; Height = BUFFER_HEIGHT / 4; Format = RGBA16F; };
texture FabulaGlowTex { Width = BUFFER_WIDTH / 8; Height = BUFFER_HEIGHT / 8; Format = RGBA16F; };
texture FabulaGlowSoftTex { Width = BUFFER_WIDTH / 8; Height = BUFFER_HEIGHT / 8; Format = RGBA16F; };
sampler FabulaQuarter { Texture = FabulaQuarterTex; };
sampler FabulaSoft { Texture = FabulaSoftTex; };
sampler FabulaGlow { Texture = FabulaGlowTex; };
sampler FabulaGlowSoft { Texture = FabulaGlowSoftTex; };

// what the frame is, off the blurred quarter: per 32x18 cell its mean luma and
// darkest channel, then the frame's veil, brightest cell and mean this frame,
// settled, and settled a frame ago. Float for the same reason XIII-2's level is
texture FabulaCellTex { Width = 32; Height = 18; Format = RGBA16F; };
texture FabulaReadTex { Width = 1; Height = 1; Format = RGBA32F; };
texture FabulaSettledTex { Width = 1; Height = 1; Format = RGBA32F; };
texture FabulaKeptTex { Width = 1; Height = 1; Format = RGBA32F; };
sampler FabulaCell { Texture = FabulaCellTex; MinFilter = POINT; MagFilter = POINT; MipFilter = POINT; };
sampler FabulaRead { Texture = FabulaReadTex; MinFilter = POINT; MagFilter = POINT; MipFilter = POINT; };
sampler FabulaSettled { Texture = FabulaSettledTex; MinFilter = POINT; MagFilter = POINT; MipFilter = POINT; };
sampler FabulaKept { Texture = FabulaKeptTex; MinFilter = POINT; MagFilter = POINT; MipFilter = POINT; };
#endif

#if FABULA_HAZE
	#define FABULA_HAZE_AMOUNT 0.45
#else
	#define FABULA_HAZE_AMOUNT 0.15
#endif

#if FABULA_HAZE || FABULA_LIFT
uniform float Haze < __UNIFORM_SLIDER_FLOAT1
	ui_min = 0.0; ui_max = 0.6;
	ui_tooltip = "How much of a hazy frame's grey veil comes off. It's measured every frame, so dark and clear places keep their look: they have none to take.";
> = FABULA_HAZE_AMOUNT;

uniform float FrameTime < source = "frametime"; >;
#endif

#if FABULA_HAZE

// the frame's darkest channel a 20px patch at a time, those averaged on a 32x18
// grid, the brightest cell in each of its rows and columns, then the grid's level
// this frame, settled, and settled a frame ago. Float, since the settling creeps
// up on its target in steps finer than half precision has near 0.3
texture FabulaDarkTex { Width = 128; Height = 72; Format = R16F; };
texture FabulaGridTex { Width = 32; Height = 18; Format = R16F; };
texture FabulaRowsTex { Width = 1; Height = 18; Format = R16F; };
texture FabulaColsTex { Width = 32; Height = 1; Format = R16F; };
texture FabulaNowTex { Width = 1; Height = 1; Format = R32F; };
texture FabulaLevelTex { Width = 1; Height = 1; Format = R32F; };
texture FabulaLastTex { Width = 1; Height = 1; Format = R32F; };
sampler FabulaDark { Texture = FabulaDarkTex; MinFilter = POINT; MagFilter = POINT; MipFilter = POINT; };
sampler FabulaGrid { Texture = FabulaGridTex; MinFilter = POINT; MagFilter = POINT; MipFilter = POINT; };
sampler FabulaRows { Texture = FabulaRowsTex; MinFilter = POINT; MagFilter = POINT; MipFilter = POINT; };
sampler FabulaCols { Texture = FabulaColsTex; MinFilter = POINT; MagFilter = POINT; MipFilter = POINT; };
sampler FabulaNow { Texture = FabulaNowTex; MinFilter = POINT; MagFilter = POINT; MipFilter = POINT; };
sampler FabulaLevel { Texture = FabulaLevelTex; MinFilter = POINT; MagFilter = POINT; MipFilter = POINT; };
sampler FabulaLast { Texture = FabulaLastTex; MinFilter = POINT; MagFilter = POINT; MipFilter = POINT; };
#endif

#include "ReShade.fxh"

#if FABULA_HAZE
float FabulaDarkPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	// 16 points across the patch, each half a pixel off so the fetch blends the
	// pixels around it rather than landing on one
	const float2 patch = float2(1.0 / 128.0, 1.0 / 72.0);
	float2 origin = texcoord - patch * 0.5 + BUFFER_PIXEL_SIZE * 0.5;
	float low = 1.0;
	[unroll] for (int y = 0; y < 4; y++)
	{
		[unroll] for (int x = 0; x < 4; x++)
		{
			float3 c = tex2Dlod(ReShade::BackBuffer, float4(origin + patch * (float2(x, y) + 0.5) * 0.25, 0.0, 0.0)).rgb;
			low = min(low, min(c.r, min(c.g, c.b)));
		}
	}
	return low;
}

float FabulaGridPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	float sum = 0.0;
	[unroll] for (int y = 0; y < 4; y++)
	{
		[unroll] for (int x = 0; x < 4; x++)
			sum += tex2Dlod(FabulaDark, float4(texcoord + (float2(x, y) - 1.5) / float2(128.0, 72.0), 0.0, 0.0)).r;
	}
	return sum / 16.0;
}

float FabulaRowsPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	float top = 0.0;
	[loop] for (int x = 0; x < 32; x++)
		top = max(top, tex2Dlod(FabulaGrid, float4((x + 0.5) / 32.0, texcoord.y, 0.0, 0.0)).r);
	return top;
}

float FabulaColsPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	float top = 0.0;
	[loop] for (int y = 0; y < 18; y++)
		top = max(top, tex2Dlod(FabulaGrid, float4(texcoord.x, (y + 0.5) / 18.0, 0.0, 0.0)).r);
	return top;
}

float FabulaNowPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	// a film's bars, or a 16:10 screen's, are black right across: the rows and
	// columns from each edge in to the first that isn't are left out, so bars
	// can't pull a hazy frame under the line on their own
	float2 low = float2(32.0, 18.0);
	float2 high = float2(-1.0, -1.0);
	[loop] for (int y = 0; y < 18; y++)
	{
		if (tex2Dlod(FabulaRows, float4(0.5, (y + 0.5) / 18.0, 0.0, 0.0)).r > 1.0 / 255.0)
		{
			low.y = min(low.y, y);
			high.y = max(high.y, y);
		}
	}
	[loop] for (int x = 0; x < 32; x++)
	{
		if (tex2Dlod(FabulaCols, float4((x + 0.5) / 32.0, 0.5, 0.0, 0.0)).r > 1.0 / 255.0)
		{
			low.x = min(low.x, x);
			high.x = max(high.x, x);
		}
	}
	// and the row or column a bar's edge cuts through, half bar and half picture,
	// so where the edge lands doesn't move the level
	low.x += low.x > 0.0 ? 1.0 : 0.0;
	low.y += low.y > 0.0 ? 1.0 : 0.0;
	high.x -= high.x < 31.0 ? 1.0 : 0.0;
	high.y -= high.y < 17.0 ? 1.0 : 0.0;

	// a harmonic mean of what's left leans on the darker cells, and the 0.05 keeps
	// one black corner, the HUD's or a black object's, from deciding it alone
	float sum = 0.0;
	float cells = 0.0;
	[loop] for (int y = 0; y < 18; y++)
	{
		[loop] for (int x = 0; x < 32; x++)
		{
			float g = tex2Dlod(FabulaGrid, float4((float2(x, y) + 0.5) / float2(32.0, 18.0), 0.0, 0.0)).r;
			float inside = x >= low.x && x <= high.x && y >= low.y && y <= high.y ? 1.0 : 0.0;
			sum += inside / (g + 0.05);
			cells += inside;
		}
	}
	return cells > 0.0 ? saturate(cells / sum - 0.05) : 0.0;
}

float FabulaLevelPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	float now = tex2Dlod(FabulaNow, float4(0.5, 0.5, 0.0, 0.0)).r;
	float last = tex2Dlod(FabulaLast, float4(0.5, 0.5, 0.0, 0.0)).r;
	last = last >= 0.0 && last <= 1.0 ? last : 0.0;

	// up slowly, so a fade in from black or a flash doesn't pump the frame, down
	// quicker. By time rather than frames, with a hitch taken as a tenth of a
	// second at most, since frametime is the frame before's
	float settle = now < last ? 150.0 : 400.0;
	return lerp(last, now, 1.0 - exp(-clamp(FrameTime, 0.0, 100.0) / settle));
}

float FabulaKeepPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	return tex2Dlod(FabulaLevel, float4(0.5, 0.5, 0.0, 0.0)).r;
}
#endif

#if FABULA_LIFT
float4 FabulaQuarterPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	// four bilinear taps a pixel off the 4x4 block's centre, each the mean of 2x2,
	// so every pixel counts once and a thin bright edge can't shimmer as it moves.
	// Spelled out, since D3D9's shader model has no integer bit ops to pick corners with
	float3 c = tex2D(ReShade::BackBuffer, texcoord + BUFFER_PIXEL_SIZE * float2(-1.0, -1.0)).rgb
	         + tex2D(ReShade::BackBuffer, texcoord + BUFFER_PIXEL_SIZE * float2(1.0, -1.0)).rgb
	         + tex2D(ReShade::BackBuffer, texcoord + BUFFER_PIXEL_SIZE * float2(-1.0, 1.0)).rgb
	         + tex2D(ReShade::BackBuffer, texcoord + BUFFER_PIXEL_SIZE * float2(1.0, 1.0)).rgb;
	c *= 0.25;
	return float4(c, dot(c, float3(0.2126, 0.7152, 0.0722)));
}

// a gaussian in six fetches a side instead of eleven, two texels a fetch: the
// bilinear filter blends each pair, so the tap sits where their weights balance
float4 Blur(sampler source, float2 texcoord, float2 step, float sigma)
{
	float4 sum = tex2D(source, texcoord);
	float total = 1.0;
	[unroll] for (int k = 1; k <= 5; k++)
	{
		float a = exp(-(2 * k - 1) * (2 * k - 1) / (2.0 * sigma * sigma));
		float b = exp(-(2 * k) * (2 * k) / (2.0 * sigma * sigma));
		float2 offset = step * ((2 * k - 1) * a + 2 * k * b) / (a + b);
		sum += (tex2D(source, texcoord + offset) + tex2D(source, texcoord - offset)) * (a + b);
		total += 2.0 * (a + b);
	}
	return sum / total;
}

float4 FabulaAcrossPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	return Blur(FabulaQuarter, texcoord, float2(4.0 * BUFFER_RCP_WIDTH, 0.0), 3.0);
}

float4 FabulaDownPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	return Blur(FabulaSoft, texcoord, float2(0.0, 4.0 * BUFFER_RCP_HEIGHT), 3.0);
}

float4 FabulaBrightPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	// LR's brightest sits around three quarters, so the glow starts at half
	float4 c = tex2D(FabulaQuarter, texcoord);
	return float4(c.rgb * smoothstep(0.5, 0.85, c.a), 1.0);
}

float4 FabulaGlowAcrossPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	return Blur(FabulaGlow, texcoord, float2(8.0 * BUFFER_RCP_WIDTH, 0.0), 4.0);
}

float4 FabulaGlowDownPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	return Blur(FabulaGlowSoft, texcoord, float2(0.0, 8.0 * BUFFER_RCP_HEIGHT), 4.0);
}

float4 FabulaCellPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	// 16 bilinear taps over the cell, off a frame already blurred past a cell's
	// width, so a lamp or a line of HUD text can't decide it on its own
	const float2 cell = float2(1.0 / 32.0, 1.0 / 18.0);
	float lit = 0.0;
	float dark = 0.0;
	[unroll] for (int y = 0; y < 4; y++)
	{
		[unroll] for (int x = 0; x < 4; x++)
		{
			float3 c = tex2Dlod(FabulaQuarter, float4(texcoord + cell * ((float2(x, y) + 0.5) / 4.0 - 0.5), 0.0, 0.0)).rgb;
			lit += dot(c, float3(0.2126, 0.7152, 0.0722));
			dark += min(c.r, min(c.g, c.b));
		}
	}
	return float4(lit, dark, 0.0, 0.0) / 16.0;
}

float4 FabulaReadPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	// the veil as XIII-2 reads it, a harmonic mean leaning on the darker cells; the
	// brightest cell, which is how much room the frame has under white; and the mean
	float top = 0.0;
	float mean = 0.0;
	float sum = 0.0;
	[loop] for (int y = 0; y < 18; y++)
	{
		[loop] for (int x = 0; x < 32; x++)
		{
			float2 c = tex2Dlod(FabulaCell, float4((float2(x, y) + 0.5) / float2(32.0, 18.0), 0.0, 0.0)).rg;
			top = max(top, c.r);
			mean += c.r;
			sum += 1.0 / (c.g + 0.05);
		}
	}
	return float4(saturate(576.0 / sum - 0.05), top, mean / 576.0, 0.0);
}

float4 FabulaSettlePass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	float4 now = tex2Dlod(FabulaRead, float4(0.5, 0.5, 0.0, 0.0));
	float4 last = tex2Dlod(FabulaKept, float4(0.5, 0.5, 0.0, 0.0));
	// the first frame, or a reload, has nothing kept worth settling from
	bool kept = all(last.rgb >= 0.0) && all(last.rgb <= 1.0) && last.a == 1.0;
	// XIII-2's pace: down in 150ms, up in 400, by time, a hitch a tenth of a second at most
	float3 settle = 400.0 - 250.0 * step(now.rgb + 1e-6, last.rgb);
	float3 eased = lerp(last.rgb, now.rgb, 1.0 - exp(-clamp(FrameTime, 0.0, 100.0) / settle));
	return float4(kept ? eased : now.rgb, 1.0);
}

float4 FabulaKeptPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	return tex2Dlod(FabulaSettled, float4(0.5, 0.5, 0.0, 0.0));
}

// LR's field HUD at 16:9, in screen fractions: the gauge with its bars bottom
// left, the map, clock and day count top right. Feathered, so the hand-off
// to the graded picture around them doesn't draw a line
float HudBox(float2 uv, float4 box)
{
	const float feather = 0.03;
	float2 inside = min(uv - box.xy, box.zw - uv) / feather;
	return smoothstep(0.0, 1.0, min(inside.x, inside.y * 9.0 / 16.0) + 0.5);
}

float HudMask(float2 uv)
{
	// anywhere but 16:9 the HUD isn't where these say, so it's left graded
	float fits = 1.0 - smoothstep(0.02, 0.04, abs(BUFFER_ASPECT_RATIO * 9.0 / 16.0 - 1.0));
	return fits * max(HudBox(uv, float4(0.015, 0.70, 0.36, 0.93)), HudBox(uv, float4(0.735, 0.065, 0.935, 0.48)));
}

// tanh from 0.85 so a bright end meets white at its slope rather than clipping
float Shoulder(float x)
{
	return x > 0.85 ? 0.85 + 0.15 * tanh((x - 0.85) / 0.15) : x;
}
#endif

float3 FabulaLousPass(float4 position : SV_Position, float2 texcoord : TexCoord) : SV_Target
{
	float3 color = tex2D(ReShade::BackBuffer, texcoord).rgb;
#if FABULA_LIFT
	float3 game = color;
#endif

#if FABULA_HAZE
	// only a frame whose darkest level is well off black is hazy. On a big drop, a
	// cut from a bright place to a dark room or a flash ending, no more of it comes
	// off than this frame has, so the dark room isn't crushed while the level comes
	// down; a dark thing crossing for a frame or two just settles, rather than
	// flicker the rest of the frame. One lift per pixel, off its darkest channel and
	// eased in from black over twice the veil, so hues stay where they were and
	// nothing under it clips, and the rest scaled back up so white stays white
	float now = tex2Dlod(FabulaNow, float4(0.5, 0.5, 0.0, 0.0)).r;
	float settled = tex2Dlod(FabulaLevel, float4(0.5, 0.5, 0.0, 0.0)).r;
	float level = lerp(settled, min(settled, now + 0.02), smoothstep(0.08, 0.16, settled - now));
	float veil = min(Haze * level * smoothstep(0.08, 0.2, level), 0.25);
	float lift = veil * smoothstep(0.0, max(2.0 * veil, 1e-5), min(color.r, min(color.g, color.b)));
	color = (color - lift) / (1.0 - lift);
#endif

#if FABULA_LIFT
	float source = dot(color, float3(0.2126, 0.7152, 0.0722));
	float sourceTop = max(color.r, max(color.g, color.b));
	float sourceTint = (sourceTop - min(color.r, min(color.g, color.b))) / max(sourceTop, 1e-5);
	float4 frame = tex2Dlod(FabulaRead, float4(0.5, 0.5, 0.0, 0.0));
	float4 steady = tex2Dlod(FabulaSettled, float4(0.5, 0.5, 0.0, 0.0));

	#if !FABULA_HAZE
	// XIII-2's veil, bounded the same way on a big drop, and only off a lit frame:
	// LR's night is meant to be night. The lower of the two means, so a cut into a
	// dark room is a dark room from its first frame
	float level = lerp(steady.r, min(steady.r, frame.r + 0.02), smoothstep(0.08, 0.16, steady.r - frame.r));
	float day = smoothstep(0.3, 0.42, min(frame.b, steady.b));
	float veil = min(Haze * level * smoothstep(0.08, 0.2, level), 0.25) * day;
	float lift = veil * smoothstep(0.0, max(2.0 * veil, 1e-5), min(color.r, min(color.g, color.b)));
	color = (color - lift) / (1.0 - lift);
	#endif

	// the shadows down off the darkest channel, eased in from black over twice
	// as far, so hues stay put and nothing under it clips
	float low = Blacks * smoothstep(0.0, max(2.0 * Blacks, 1e-5), min(color.r, min(color.g, color.b)));
	color = (color - low) / (1.0 - low);

	// the top stretched on luma, so a colour keeps its hue, by however far the
	// frame's brightest region is from white and never more than Whites allows. Off
	// the settled read only: taking this frame's on the way up flicked the whole
	// frame down every time a fast pan slid sky into a cell, then let it creep back.
	// The shoulder is scaled so white still lands on white, so a flash the settled
	// read hasn't caught up with yet can't clip, it only rounds off a touch more
	float stretch = clamp(0.95 / max(steady.g, 1e-3), 1.0, 1.0 / (1.0 - Whites));
	float lit = dot(color, float3(0.2126, 0.7152, 0.0722));
	float shoulder = Shoulder(lit * stretch) / Shoulder(stretch);
	color *= shoulder / max(lit, 1e-5);

	// a saturated channel the scale took past white comes back toward luma
	// rather than clip, so a red lamp stays red instead of going orange
	float peak = max(color.r, max(color.g, color.b));
	color = peak > 1.0 ? shoulder + (color - shoulder) * saturate((1.0 - shoulder) / max(peak - shoulder, 1e-5)) : color;
#endif

	float luma = dot(color, float3(0.2126, 0.7152, 0.0722));

	// Curves' smoothstep as an offset, negative under mid-grey and positive over it.
	// luma^6 * (1 - luma) is 0 at black and at white and peaks at 6/7, and 7^7 / 6^6
	// scales that peak to 1, so Highlights reads straight in luma
	float square = luma * luma;
	float bump = square * square * square * (1.0 - luma) * 17.651385;
	float graded = luma + Contrast * luma * (1.0 - luma) * (2.0 * luma - 1.0) - Highlights * bump;
	color += graded - luma;

	// the duller the colour the bigger the push, faded out over the brightest third
	// so the haze and the white rooms don't get tinted on top of everything else
	float top = max(color.r, max(color.g, color.b));
	float bottom = min(color.r, min(color.g, color.b));
	float push = Vibrance * (1.0 - (top - bottom)) * (1.0 - smoothstep(0.65, 1.0, graded));

	// eased out over the last quarter of the way to black or white, so a dark blue
	// doesn't flick between no push and a full one a level apart, and up to Vibrance
	// 0.25 every channel stays inside. The shift above can still take a saturated
	// colour's channel past black or white, as Curves does: no push there, and it clips
	float edge = saturate(4.0 * min(bottom / max(top, 1e-5), (1.0 - top) / max(1.0 - bottom, 1e-5)));
	color = graded + (color - graded) * (1.0 + push * edge);

#if FABULA_LIFT
	// white cloth and stone in shade, lit by the sky, are a little blue in the
	// game, and taking haze off and pushing vibrance makes that read as blue
	// cloth. What the game drew near neutral stays as near neutral as it drew
	// it. Mid tones only: a pale sky going bluer is the haze coming off
	float neutral = (1.0 - smoothstep(0.06, 0.18, sourceTint)) *
		smoothstep(0.25, 0.4, source) * (1.0 - smoothstep(0.7, 0.85, source));
	float nowTop = max(color.r, max(color.g, color.b));
	float nowSpread = nowTop - min(color.r, min(color.g, color.b));
	float keep = min(1.0, sourceTint * nowTop / max(nowSpread, 1e-5));
	float nowLuma = dot(color, float3(0.2126, 0.7152, 0.0722));
	color = nowLuma + (color - nowLuma) * lerp(1.0, keep, neutral);

	// the detail measured on the frame as it came in, before the stretch made it
	// bigger, and squashed so a hard edge (a sign, the HUD) gets far less than its
	// size. Weighted to the mids, where it's texture rather than a highlight
	float detail = source - tex2D(FabulaQuarter, texcoord).a;
	detail /= 1.0 + 4.0 * abs(detail);
	float mid = dot(color, float3(0.2126, 0.7152, 0.0722));
	color += Clarity * detail * 4.0 * saturate(mid) * saturate(1.0 - mid);

	// screened, so it can take a pixel toward white but never past it, and
	// stretched the way the frame's top was so it matches what it glows off. A
	// lamp in the dark glows; a frame lit everywhere would only wash out
	float3 glow = saturate(tex2D(FabulaGlow, texcoord).rgb * stretch);
	color = 1.0 - (1.0 - saturate(color)) * (1.0 - Glow * (1.0 - smoothstep(0.3, 0.55, steady.b)) * glow);

	// behind the HUD the haze coming off and the added texture put a busy,
	// darker ground under a dark gauge and outlined numbers. The game's softer
	// picture there is what the HUD was drawn against
	color = lerp(saturate(color), game, Hud * HudMask(texcoord));
#endif

	return saturate(color);
}

#if FABULA_HAZE
technique FabulaLous < ui_label = "Fabula-Lous XIII-2"; ui_tooltip = "The preset's grade for XIII-2: the veil off hazy places, contrast, vibrance."; >
#else
	#if FABULA_LIFT
technique FabulaLous < ui_label = "Fabula-Lous LR"; ui_tooltip = "The preset's grade for Lightning Returns, read off each frame: the top stretched as far as it has room, a lit frame's haze off, the shadows down, local contrast, a glow at night, contrast, vibrance."; >
	#else
technique FabulaLous < ui_label = "Fabula-Lous"; ui_tooltip = "The preset's grade: contrast, a calmer bright end, vibrance."; >
	#endif
#endif
{
#if FABULA_HAZE
	pass Dark
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaDarkPass;
		RenderTarget = FabulaDarkTex;
	}
	pass Grid
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaGridPass;
		RenderTarget = FabulaGridTex;
	}
	pass Rows
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaRowsPass;
		RenderTarget = FabulaRowsTex;
	}
	pass Cols
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaColsPass;
		RenderTarget = FabulaColsTex;
	}
	pass Now
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaNowPass;
		RenderTarget = FabulaNowTex;
	}
	pass Level
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaLevelPass;
		RenderTarget = FabulaLevelTex;
	}
	pass Keep
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaKeepPass;
		RenderTarget = FabulaLastTex;
	}
#endif
#if FABULA_LIFT
	pass Quarter
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaQuarterPass;
		RenderTarget = FabulaQuarterTex;
	}
	pass Across
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaAcrossPass;
		RenderTarget = FabulaSoftTex;
	}
	pass Down
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaDownPass;
		RenderTarget = FabulaQuarterTex;
	}
	pass Cell
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaCellPass;
		RenderTarget = FabulaCellTex;
	}
	pass Read
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaReadPass;
		RenderTarget = FabulaReadTex;
	}
	pass Settle
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaSettlePass;
		RenderTarget = FabulaSettledTex;
	}
	pass Kept
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaKeptPass;
		RenderTarget = FabulaKeptTex;
	}
	pass Bright
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaBrightPass;
		RenderTarget = FabulaGlowTex;
	}
	pass GlowAcross
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaGlowAcrossPass;
		RenderTarget = FabulaGlowSoftTex;
	}
	pass GlowDown
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaGlowDownPass;
		RenderTarget = FabulaGlowTex;
	}
#endif
	pass
	{
		VertexShader = PostProcessVS;
		PixelShader = FabulaLousPass;
	}
}
