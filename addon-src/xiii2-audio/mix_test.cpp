// The audio fix's sums, checked natively by tools/build_xiii2_audio.sh before
// it builds the .asi, since nobody can hear a 7.1 rig from CI.

#include "mix.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>

namespace {

int g_failed = 0;

void Check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++g_failed;
    }
}

bool Near(float a, float b, float eps = 1e-5f) { return std::fabs(a - b) <= eps; }

// what the game's SetFrequency makes of an XAudio2 frequency, in float the way
// it does it (asin in double, scaled by 1/pi in float)
float Normalized(float f) {
    return float(std::asin(double(f) * 0.5)) * 0.31830987f;
}

void FilterTop() {
    Check(mix::Openness(Normalized(1.0f)) == 1.0f, "the top filter setting is fully open");
    Check(mix::Openness(0.25f) == 1.0f, "so is the filter's constructor default");
    Check(mix::Openness(Normalized(0.5f)) == 0.0f, "a real low pass stays exactly the game's");
    Check(mix::Openness(Normalized(0.85f)) == 0.0f, "and so does one well short of the top");
    // a sweep up to the top opens steadily, never in one jump
    float last = 0.0f, biggest = 0.0f;
    for (int i = 0; i <= 1000; ++i) {
        const float t = mix::Openness(Normalized(0.85f + 0.15f * i / 1000.0f));
        Check(t >= last, "opening never goes backwards on the way up");
        biggest = std::fmax(biggest, t - last);
        last = t;
    }
    Check(biggest < 0.05f, "no step bigger than 5% between neighbouring settings");
    // the game's open low pass at 48 kHz, from the emulated original
    float c[5] = {0.1365f, 0.2729f, 0.1365f, 0.6823f, -0.2281f};
    mix::Open(c, 1.0f);
    Check(c[0] == 1.0f && c[1] == 0.0f && c[2] == 0.0f && c[3] == 0.0f && c[4] == 0.0f,
          "fully open is y = x");
    float half[5] = {0.1365f, 0.2729f, 0.1365f, 0.6823f, -0.2281f};
    mix::Open(half, 0.5f);
    const float a1 = half[3], a2 = half[4];
    Check(std::fabs(a2) < 1.0f && std::fabs(a1) < 1.0f - a2, "half open is still a stable filter");
    Check(Near(half[0] + half[1] + half[2], (1.0f - a1 - a2), 1e-4f), "with no change in level at DC");
}

void StereoMatrix() {
    mix::Limiter lim;
    lim.Rate(48000);
    float out[2];
    const float centre[6] = {0, 0, 0.5f, 0, 0, 0};
    mix::Frame(centre, out, 2, 0, lim);
    Check(Near(out[0], 0.5f * mix::kHalf) && Near(out[0], out[1]), "centre splits evenly at -3 dB");
    const float left[6] = {0.5f, 0, 0, 0, 0, 0};
    mix::Frame(left, out, 2, 0, lim);
    Check(Near(out[0], 0.5f) && out[1] == 0.0f, "front left stays left, no crossfeed");
    const float surround[6] = {0, 0, 0, 0, 0, 0.4f};
    mix::Frame(surround, out, 2, 0, lim);
    Check(out[0] == 0.0f && Near(out[1], 0.4f * mix::kHalf), "right surround folds into the right");
}

void LimiterHolds() {
    mix::Limiter lim;
    lim.Rate(48000);
    float out[2];
    // the frame from the emulated game mixdown that came out at 1.07
    const float loud[6] = {0.1f, 0.2f, 0.3f, 0.4f, 0.5f, 0.6f};
    float worst = 0.0f;
    for (int i = 0; i < 4800; ++i) {
        const float everything[6] = {1, 1, 1, 1, 1, 1};
        mix::Frame(i % 2 ? loud : everything, out, 2, 0, lim);
        worst = std::fmax(worst, std::fmax(std::fabs(out[0]), std::fabs(out[1])));
    }
    Check(worst <= mix::Limiter::kCeiling + 1e-6f, "nothing gets past the ceiling, every channel at full scale");
    // a quiet stretch after it brings the gain back
    const float quiet[6] = {0.1f, 0.1f, 0, 0, 0, 0};
    for (int i = 0; i < 48000; ++i) mix::Frame(quiet, out, 2, 0, lim);
    Check(lim.gain > 0.99f, "the gain is back within a second of quiet");
    Check(Near(out[0], 0.1f, 1e-3f), "and quiet passes through untouched");

    // the same recovery through silent buffers, which never reach Next
    const float everything[6] = {1, 1, 1, 1, 1, 1};
    mix::Frame(everything, out, 2, 0, lim);
    for (int i = 0; i < 100; ++i) lim.Idle(480);
    Check(lim.gain > 0.99f, "a second of silence brings the gain back too");

    // a stray inf doesn't mute what's playing
    lim.gain = 1.0f;
    const float broken[6] = {INFINITY, 0.1f, 0, 0, 0, 0};
    mix::Frame(broken, out, 2, 0, lim);
    Check(lim.gain == 1.0f && Near(out[1], 0.1f), "an inf passes through without taking the gain with it");
}

void SevenOne() {
    mix::Limiter lim;
    const float in[6] = {0.1f, 0.2f, 0.3f, 0.4f, 0.5f, 0.6f};
    float out[8];
    for (float& v : out) v = NAN;
    mix::Frame(in, out, 8, 0x63F, lim);
    for (int i = 0; i < 8; ++i) Check(!std::isnan(out[i]), "every one of 7.1's channels is written");
    Check(out[0] == 0.1f && out[3] == 0.4f, "fronts and LFE land where they were");
    Check(Near(out[4], 0.5f * mix::kHalf) && Near(out[6], 0.5f * mix::kHalf), "left surround shared across back and side");
    Check(Near(out[4] * out[4] + out[6] * out[6], 0.25f), "at the power it had");

    for (float& v : out) v = NAN;
    mix::Frame(in, out, 8, 0xFF, lim);  // the old wide 7.1, no side pair
    Check(out[4] == 0.5f && out[5] == 0.6f && out[6] == 0.0f && out[7] == 0.0f,
          "wide 7.1 keeps surrounds on the backs and silences the extra fronts");
}

void Layouts() {
    Check(mix::Folds(2, 0x3) && mix::Folds(1, 0x4) && mix::Folds(4, 0x33), "stereo, mono and quad fold");
    Check(!mix::Folds(3, 0xB) && !mix::Folds(5, 0x37) && !mix::Folds(4, 0x107),
          "2.1, 5.0 and front-and-back-centre quad stay the game's");
}

void FiveOne() {
    mix::Limiter lim;
    const float in[6] = {0.1f, 0.2f, 0.3f, 0.4f, 0.5f, 0.6f};
    float out[6];
    mix::Frame(in, out, 6, 0x3F, lim);
    for (int i = 0; i < 6; ++i) Check(out[i] == in[i], "5.1 to 5.1 is a straight copy");
}

void Quad() {
    mix::Limiter lim;
    lim.Rate(48000);
    const float in[6] = {0, 0, 0.2f, 0, 0.3f, 0.4f};
    float out[4];
    mix::Frame(in, out, 4, 0x33, lim);
    Check(Near(out[0], 0.2f * mix::kHalf) && Near(out[2], 0.3f) && Near(out[3], 0.4f),
          "quad folds the centre into the fronts and keeps the surrounds behind");
}

}  // namespace

int main() {
    FilterTop();
    StereoMatrix();
    LimiterHolds();
    SevenOne();
    Layouts();
    FiveOne();
    Quad();
    if (g_failed) return EXIT_FAILURE;
    std::printf("mix ok\n");
    return EXIT_SUCCESS;
}
