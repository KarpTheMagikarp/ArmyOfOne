// Audio fixes for XIII-2, as a NovaFix add-on. Three things in the Steam
// build's sound driver are broken, and all three are fixed where they happen:
//
//   Muffle: every voice runs through the game's own IIR filter XAPO. Its
//   SetFrequency takes XAudio2's units, where 1.0 is the top, and turns that
//   into asin(F/2)/pi. On XAudio2's filter 1.0 means no filtering. On this
//   biquad it's a real low pass at a sixth of the sample rate, about -4 dB at
//   8 kHz and -20 dB at 16 kHz at 48 kHz, and it's where voices sit unless a
//   sound asks for less. The low pass coefficient call (0x70C1F0, slot 15 of
//   the filter's vtable) now hands back a pass-through biquad at that top
//   setting, faded in over the last stretch below it so a sweep doesn't jump.
//   Anything a sound properly dials down is left alone.
//
//   Clipping: on stereo the master bus ends in the game's StereoMixdownAPO,
//   whose matrix (0x22C9228) sums to nearly 3x full scale per side with no
//   limiting. Its Process (0x728590) is replaced with an ITU style downmix
//   and a peak limiter.
//
//   7.1 buzz: that same APO is in the chain on every device, switched off
//   unless the device is stereo, and switched off it copies the game's 6
//   channels into the device's 8 and never writes the last 2. Whatever was in
//   the buffer plays out of the side speakers. The replacement writes every
//   channel, and shares the surrounds with the side pair on a 7.1 that has
//   one.
//
// Pre-rendered cutscenes play through Bink's own waveOut output, outside all
// of this, so they're still stereo.
//
// cyanea-bt's FFXIII-2 AudioFix covers the same ground, so if it's in, beside
// the exe or anywhere in the add-on folder, this stays off and lets it work.
//
// Each fix can be turned off in xiii2-audio.ini. Only the Steam
// ffxiii2img.exe is supported: every hook site's bytes are checked first, and
// anything else gets a line in xiii2-audio.log and an add-on that does nothing.

#include <windows.h>

#include <MinHook.h>

#include <atomic>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <string>

#include "mix.h"

namespace {

std::wstring g_dir;

void Log(const char* format, ...) {
    FILE* file = nullptr;
    if (_wfopen_s(&file, (g_dir + L"\\xiii2-audio.log").c_str(), L"a") != 0 || !file) return;
    va_list args;
    va_start(args, format);
    vfprintf(file, format, args);
    va_end(args);
    fputc('\n', file);
    fclose(file);
}

// RVAs in the Steam build (ffxiii2img.exe, sha256 2ef51138...). The image
// moves with ASLR, so everything is base + RVA

// FilterBase's low pass slot: thiscall (out, freq, 1/Q, gain), ret 0x10. The
// whole wrapper, call into the coefficient maths included
constexpr DWORD kLowPassRva = 0x70C1F0;
constexpr unsigned char kLowPassHead[] = {
    0xD9, 0x44, 0x24, 0x0C, 0x8B, 0x44, 0x24, 0x04, 0x83, 0xEC, 0x08, 0xD9, 0x5C, 0x24, 0x04, 0xD9,
    0x44, 0x24, 0x10, 0xD9, 0x1C, 0x24, 0x50, 0xE8, 0x44, 0xFB, 0xFF, 0xFF, 0x83, 0xC4, 0x0C, 0xC2,
    0x10, 0x00};

// StereoMixdownAPO::Process, as far as its reads of the two channel counts
constexpr DWORD kMixdownRva = 0x728590;
constexpr unsigned char kMixdownHead[] = {
    0x55, 0x8B, 0x6C, 0x24, 0x10, 0x83, 0x7D, 0x04, 0x01, 0x0F, 0x85, 0xAF, 0x01, 0x00, 0x00, 0x53,
    0x33, 0xD2, 0x56, 0x57, 0x39, 0x54, 0x24, 0x28, 0x0F, 0x84, 0x39, 0x01, 0x00, 0x00, 0x8B, 0x5C,
    0x24, 0x14, 0x8B, 0x44, 0x24, 0x24, 0x0F, 0xB7, 0x4B, 0x34};

// where the driver keeps the chosen device's details: mov eax, [esp+0x43C]
// (the speaker mask), then the stores of rate, channels and mask
constexpr DWORD kMaskStoreRva = 0x6F2861;
constexpr unsigned char kMaskStoreHead[] = {0x8B, 0x84, 0x24, 0x3C, 0x04, 0x00, 0x00, 0x89, 0x0D};
constexpr DWORD kMaskImm = 0x6F2874 + 1;  // mov [mask], eax

// in the APO, from its LockForProcess: the input format's copy at +0x20 and
// the output's at +0x32
constexpr DWORD kInChannels = 0x22, kInRate = 0x24, kOutChannels = 0x34;

struct Buffer {  // XAPO_PROCESS_BUFFER_PARAMETERS
    void* data;
    UINT32 flags;
    UINT32 frames;
};
constexpr UINT32 kValid = 1;  // XAPO_BUFFER_VALID

BYTE* g_base = nullptr;
const DWORD* g_mask = nullptr;

using LowPassFn = void(__fastcall*)(void*, void*, float*, float, float, float);
using MixdownFn = HRESULT(__stdcall*)(BYTE*, UINT32, const Buffer*, UINT32, Buffer*, BOOL);
LowPassFn g_lowPass = nullptr;
MixdownFn g_mixdown = nullptr;

bool g_filter = true, g_downmix = true, g_surround = true;

void __fastcall LowPassHook(void* self, void* edx, float* coeffs, float freq, float q, float gain) {
    g_lowPass(self, edx, coeffs, freq, q, gain);
    const float t = g_filter ? mix::Openness(freq) : 0.0f;
    if (t > 0.0f) mix::Open(coeffs, t);
}

// one per APO, and the master bus only ever has the one. Process runs on
// XAudio2's thread alone, so nothing here needs a lock
struct Slot {
    BYTE* apo = nullptr;
    mix::Limiter limiter;
};
Slot g_slots[4];

mix::Limiter& LimiterFor(BYTE* apo, UINT32 rate) {
    for (Slot& s : g_slots)
        if (s.apo == apo) return s.limiter;
    for (Slot& s : g_slots)
        if (!s.apo) {
            s.apo = apo;
            s.limiter.Rate(rate);
            return s.limiter;
        }
    // a fifth one is more APOs than the game makes. Reuse the last, a shared
    // limiter is still better than none
    return g_slots[3].limiter;
}

// the first buffer of each kind gets a line, so the log says what the run
// actually did without the audio thread writing to disk every buffer
std::atomic<unsigned> g_logged{0};

bool First(unsigned bit) {
    return !(g_logged.fetch_or(bit) & bit);
}

HRESULT __stdcall MixdownHook(BYTE* apo, UINT32 inCount, const Buffer* in, UINT32 outCount, Buffer* out,
                              BOOL enabled) {
    const unsigned ic = *reinterpret_cast<WORD*>(apo + kInChannels);
    const unsigned oc = *reinterpret_cast<WORD*>(apo + kOutChannels);
    const DWORD mask = g_mask ? *g_mask : 0;
    // the game turns the APO on to ask for stereo, and a device with fewer
    // than 5.1's channels needs folding down whatever it asked
    const bool fold = enabled || oc < 6;
    // only ever fed the game's 5.1 bus, and 5.1 out unfolded is a straight
    // copy the game already gets right. Anything else isn't ours to guess at
    if (ic != 6 || oc == 0 || oc > 8 || (!fold && oc == 6) || (!enabled && oc < 6 && !mix::Folds(oc, mask)) ||
        !(fold ? g_downmix : g_surround))
        return g_mixdown(apo, inCount, in, outCount, out, enabled);

    mix::Limiter& lim = LimiterFor(apo, *reinterpret_cast<DWORD*>(apo + kInRate));
    out->frames = in->frames;
    out->flags = in->flags;
    if (in->flags != kValid) {
        lim.Idle(in->frames);
        return S_OK;
    }
    const float* src = static_cast<const float*>(in->data);
    float* dst = static_cast<float*>(out->data);

    if (enabled && oc > 2) {
        // stereo asked for on a bigger device: the fronts and silence
        // elsewhere, as the game meant, minus the clipping
        if (First(1)) Log("%u channel device with the game's stereo downmix on: fronts only, limited", oc);
        for (UINT32 f = 0; f < in->frames; ++f, src += ic, dst += oc) {
            mix::Frame(src, dst, 2, mask, lim);
            for (unsigned c = 2; c < oc; ++c) dst[c] = 0.0f;
        }
        return S_OK;
    }

    if (fold) {
        if (First(2)) Log("%u channel device: 5.1 folded down and limited", oc);
    } else if (First(4)) {
        Log("%u channel device, speaker mask 0x%lX: every channel written", oc, mask);
    }
    for (UINT32 f = 0; f < in->frames; ++f, src += ic, dst += oc) mix::Frame(src, dst, oc, mask, lim);
    return S_OK;
}

bool Matches(DWORD rva, const unsigned char* head, size_t n, const char* what) {
    auto* dos = reinterpret_cast<IMAGE_DOS_HEADER*>(g_base);
    auto* nt = reinterpret_cast<IMAGE_NT_HEADERS*>(g_base + dos->e_lfanew);
    if (rva + n <= nt->OptionalHeader.SizeOfImage && memcmp(g_base + rva, head, n) == 0) return true;
    Log("this ffxiii2img.exe isn't the Steam build the audio fix was made for (%s doesn't match), "
        "so it's staying off", what);
    return false;
}

bool Verify() {
    g_base = reinterpret_cast<BYTE*>(GetModuleHandleW(nullptr));
    if (!Matches(kLowPassRva, kLowPassHead, sizeof kLowPassHead, "voice filter") ||
        !Matches(kMixdownRva, kMixdownHead, sizeof kMixdownHead, "stereo mixdown") ||
        !Matches(kMaskStoreRva, kMaskStoreHead, sizeof kMaskStoreHead, "speaker mask") ||
        !Matches(kMaskImm - 1, reinterpret_cast<const unsigned char*>("\xA3"), 1, "speaker mask"))
        return false;
    g_mask = *reinterpret_cast<const DWORD**>(g_base + kMaskImm);
    return true;
}

// off the name in its version info, read straight out of the file. Asking the
// loader for it doesn't work: theirs is called winmm.dll, and the loader (and
// Wine, under Proton) hands back the system's winmm for that name instead
bool IsTheirAudioFix(const std::wstring& path) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr,
                              OPEN_EXISTING, 0, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    std::string bytes;
    const DWORD size = GetFileSize(file, nullptr);
    DWORD got = 0;
    if (size != INVALID_FILE_SIZE && size < (64u << 20)) {
        bytes.resize(size);
        if (!ReadFile(file, bytes.data(), size, &got, nullptr)) got = 0;
    }
    CloseHandle(file);
    bytes.resize(got);
    auto wide = [](const wchar_t* text) {
        return std::string(reinterpret_cast<const char*>(text), wcslen(text) * sizeof(wchar_t));
    };
    static const std::string name = wide(L"FFXIII-2 AudioFix"), product = wide(L"ProductName"),
                             description = wide(L"FileDescription");
    // only as the value of one of those two keys, so a file that merely
    // mentions it (this one does, just above) isn't mistaken for it
    for (size_t at = bytes.find(name); at != std::string::npos; at = bytes.find(name, at + 2)) {
        const std::string before = bytes.substr(at < 64 ? 0 : at - 64, at < 64 ? at : 64);
        if (before.find(product) != std::string::npos || before.find(description) != std::string::npos)
            return true;
    }
    return false;
}

// their winmm.dll beside the exe, or in the add-on folder or one below it
std::wstring FindTheirAudioFix(const std::wstring& dir, int depth) {
    WIN32_FIND_DATAW found;
    HANDLE search = FindFirstFileW((dir + L"\\*").c_str(), &found);
    if (search == INVALID_HANDLE_VALUE) return {};
    std::wstring hit;
    do {
        const std::wstring name = found.cFileName, path = dir + L"\\" + name;
        if (found.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            if (depth > 0 && name != L"." && name != L"..") hit = FindTheirAudioFix(path, depth - 1);
            continue;
        }
        const size_t dot = name.find_last_of(L'.');
        const std::wstring ext = dot == std::wstring::npos ? L"" : name.substr(dot);
        if ((_wcsicmp(ext.c_str(), L".dll") == 0 || _wcsicmp(ext.c_str(), L".asi") == 0) && IsTheirAudioFix(path))
            hit = path;
    } while (hit.empty() && FindNextFileW(search, &found));
    FindClose(search);
    return hit;
}

bool TheirsIsIn() {
    wchar_t exe[MAX_PATH]{};
    GetModuleFileNameW(nullptr, exe, MAX_PATH);
    std::wstring bin = exe;
    bin.resize(bin.find_last_of(L"\\/"));
    // ours sits in its own folder under the add-on folder NovaFix reads
    const std::wstring addons = g_dir.substr(0, g_dir.find_last_of(L"\\/"));
    std::wstring theirs = FindTheirAudioFix(bin, 0);
    if (theirs.empty()) theirs = FindTheirAudioFix(addons, 1);
    if (theirs.empty()) return false;
    Log("FFXIII-2 AudioFix is installed (%ls) and fixes the same things, so this one is staying off", theirs.c_str());
    return true;
}

bool Hook(LPVOID target, LPVOID detour, LPVOID* original) {
    return MH_CreateHook(target, detour, original) == MH_OK && MH_EnableHook(target) == MH_OK;
}

void ReadSettings() {
    const std::wstring ini = g_dir + L"\\xiii2-audio.ini";
    g_filter = GetPrivateProfileIntW(L"Audio", L"ClearVoices", 1, ini.c_str()) != 0;
    g_downmix = GetPrivateProfileIntW(L"Audio", L"StereoLimiter", 1, ini.c_str()) != 0;
    g_surround = GetPrivateProfileIntW(L"Audio", L"Surround", 1, ini.c_str()) != 0;
    Log("settings: clear voices %d, stereo limiter %d, surround %d", g_filter, g_downmix, g_surround);
}

DWORD WINAPI Worker(LPVOID) {
    Log("xiii2_audio 1.0, pid %lu", GetCurrentProcessId());
    if (TheirsIsIn() || !Verify()) return 0;
    ReadSettings();
    if (MH_Initialize() != MH_OK) {
        Log("couldn't start MinHook, the audio fix is off this run");
        return 0;
    }
    // each fix stands alone, so one failing doesn't cost the others
    if (g_filter && !Hook(g_base + kLowPassRva, reinterpret_cast<LPVOID>(&LowPassHook),
                          reinterpret_cast<LPVOID*>(&g_lowPass)))
        Log("couldn't hook the voice filter, voices stay as the game has them");
    if ((g_downmix || g_surround) && !Hook(g_base + kMixdownRva, reinterpret_cast<LPVOID>(&MixdownHook),
                                           reinterpret_cast<LPVOID*>(&g_mixdown)))
        Log("couldn't hook the mixdown, stereo and 7.1 stay as the game has them");
    Log("ready");
    return 0;
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason != DLL_PROCESS_ATTACH) return TRUE;
    DisableThreadLibraryCalls(module);
    wchar_t path[MAX_PATH]{};
    GetModuleFileNameW(module, path, MAX_PATH);
    g_dir = path;
    g_dir.resize(g_dir.find_last_of(L"\\/"));
    DeleteFileW((g_dir + L"\\xiii2-audio.log").c_str());
    // never in the loader lock: MinHook suspends threads to patch
    HANDLE thread = CreateThread(nullptr, 0, Worker, nullptr, 0, nullptr);
    if (thread) CloseHandle(thread);
    return TRUE;
}
