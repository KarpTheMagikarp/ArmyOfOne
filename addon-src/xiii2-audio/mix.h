// The sums behind the XIII-2 audio fix, kept apart from the hooks so they
// build and get tested anywhere (mix_test.cpp), not only inside the game.

#pragma once

#include <cmath>
#include <cstdint>

namespace mix {

// the game's 5.1 bus, in order
enum { FL, FR, FC, LFE, BL, BR };

// asin(0.5) / pi: what an XAudio2 filter frequency of 1.0 comes out as once
// the game's SetFrequency is done with it, a hair under so float rounding
// can't step over it
constexpr float kOpen = 0.1666f;

// where the fade to pass-through starts, an XAudio2 frequency of about 0.91
constexpr float kOpening = 0.15f;

// XAudio2 treats a low pass at frequency 1.0 as no filter at all. The game
// ported that over to a biquad of its own, where 1.0 is a real filter at a
// sixth of the sample rate. Every voice sits there by default, which is the
// muffle. 0 is the game's filter as it is, 1 is no filter. The last stretch
// below the top fades between them, so a sound sweeping its filter open
// doesn't jump from muffled to clear in one step
inline float Openness(float normalized) {
    if (normalized >= kOpen) return 1.0f;
    if (normalized <= kOpening) return 0.0f;
    return (normalized - kOpening) / (kOpen - kOpening);
}

// toward a pass-through biquad, which is just b0 = 1. Stable filters form a
// convex region in (a1, a2), so a blend of the game's and that one stays stable
inline void Open(float* c, float t) {
    const float keep = 1.0f - t;
    c[0] = keep * c[0] + t;
    for (int i = 1; i < 5; ++i) c[i] *= keep;
}

constexpr float kHalf = 0.70710678f;  // -3 dB, the usual centre and surround share
// the game's own LFE share. Whatever bass the sound team put down there
// stays as loud as they had it
constexpr float kLfe = 0.385f;

// ITU style: each front to its own side, centre and surrounds at -3 dB. The
// game's matrix had all of that plus louder surrounds and an 11% crossfeed,
// summing to nearly 3x full scale per side, which is the clipping
inline void Stereo(const float* in, float& l, float& r) {
    const float shared = kHalf * in[FC] + kLfe * in[LFE];
    l = in[FL] + shared + kHalf * in[BL];
    r = in[FR] + shared + kHalf * in[BR];
}

// a peak limiter instead of letting the sum clip. It ducks the instant a
// frame would go over and comes back up over about a fifth of a second, so
// a loud moment gets quieter rather than distorted
struct Limiter {
    static constexpr float kCeiling = 0.97f;
    float gain = 1.0f;
    float release = 0.0f;

    void Rate(uint32_t hz) {
        release = 1.0f - std::exp(-1.0f / (0.2f * float(hz ? hz : 48000)));
    }

    float Next(float peak) {
        gain += (1.0f - gain) * release;
        // a stray inf on the bus would take the gain to 0 and mute everything
        // for half a second. It plays as it would have without us
        if (std::isfinite(peak) && peak * gain > kCeiling) gain = kCeiling / peak;
        return gain;
    }

    // silent buffers never reach Next, but they're time passing all the same.
    // Without this a loud fight ending on a load screen ducks the first line
    // of the next scene
    void Idle(uint32_t frames) {
        float step = 1.0f - release, left = 1.0f;
        for (uint32_t n = frames; n; n >>= 1, step *= step)
            if (n & 1) left *= step;
        gain = 1.0f - (1.0f - gain) * left;
    }
};

// the layouts Frame folds 5.1 down to. Anything else under 6 channels (2.1,
// 5.0, quad with a back centre) is left to the game, which is no worse than
// before
inline bool Folds(unsigned outChannels, uint32_t mask) {
    return outChannels == 1 || outChannels == 2 || (outChannels == 4 && (mask == 0 || mask == 0x33));
}

// what one frame of the game's 5.1 becomes on out channels. mask is the
// device's speaker mask, for telling 7.1's side pair from the old wide one
inline void Frame(const float* in, float* out, unsigned outChannels, uint32_t mask, Limiter& lim) {
    float peak = 0.0f;
    switch (outChannels) {
    case 1: {
        float l, r;
        Stereo(in, l, r);
        out[0] = 0.5f * (l + r);
        peak = std::fabs(out[0]);
        break;
    }
    case 2:
        Stereo(in, out[0], out[1]);
        peak = std::fmax(std::fabs(out[0]), std::fabs(out[1]));
        break;
    case 4: {
        // quad has no centre or LFE of its own, so they fold into the fronts
        const float shared = kHalf * in[FC] + kLfe * in[LFE];
        out[0] = in[FL] + shared;
        out[1] = in[FR] + shared;
        out[2] = in[BL];
        out[3] = in[BR];
        for (int i = 0; i < 4; ++i) peak = std::fmax(peak, std::fabs(out[i]));
        break;
    }
    default: {
        // 5.1 and up: the game's channels land where they always did. What's
        // left used to be whatever was in the buffer already, which on 7.1 is
        // the buzz
        const unsigned n = outChannels < 6 ? outChannels : 6;
        for (unsigned i = 0; i < n; ++i) out[i] = in[i];
        for (unsigned i = n; i < outChannels; ++i) out[i] = 0.0f;
        // 7.1's sides sit beside you, where 5.1's surrounds are meant to be,
        // so they're shared across both pairs at equal power. 0x600 is the
        // side pair, and no mask at all means Windows' usual 7.1
        const bool sides = mask == 0 || (mask & 0x600) == 0x600;
        if (outChannels == 8 && sides) {
            out[BL] = kHalf * in[BL];
            out[BR] = kHalf * in[BR];
            out[6] = kHalf * in[BL];
            out[7] = kHalf * in[BR];
        }
        return;  // nothing summed, so nothing to limit
    }
    }
    const float g = lim.Next(peak);
    if (g < 1.0f)
        for (unsigned i = 0; i < outChannels; ++i) out[i] *= g;
}

}  // namespace mix
