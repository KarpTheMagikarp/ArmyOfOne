// Nova Chrysalia's code patches, applied by a NovaFix add-on instead of Nova.
//
// Nova launches the exe itself, suspends it and writes each .nccp's bytes into
// the game's memory at its image base plus the RVA. We can't launch it: the
// Steam builds refuse anything Steam didn't start (patcher/launch.py). So the
// bytes go in from inside, when NovaFix loads its add-ons. That's later than
// Nova: NovaFix 1.0.2 runs addon_loader::Initialize() from its one-time
// Initialize(), which only EnsureInitialized() reaches, from the D3D9 proxy
// exports and its GetProcAddress hook. So add-ons load at the game's first
// Direct3DCreate9, render init, after the entry point and the startup code.
// A patch to code that already ran by then won't take; gameplay code is fine.
// The exe on disk never changes, CEG's self-check on XIII reads Steam's bytes,
// and taking a mod off is just its .nccp leaving this folder.
//
// Every .nccp beside this file goes in, in name order, the way Nova's
// Patches\ folder did. ArmyOfOne writes them here, one per mod, and turns away
// a mod whose patches overlap one already in, so the order never decides.

#include "nccp.h"

#include <windows.h>

#include <algorithm>
#include <cstdio>
#include <cwchar>
#include <string>
#include <vector>

namespace {

std::wstring g_folder;

void Log(const wchar_t* format, ...) {
    if (g_folder.empty()) return;
    std::wstring path = g_folder + L"\\ff13-code-patches.log";
    FILE* file = _wfopen(path.c_str(), L"a");
    if (!file) return;
    va_list args;
    va_start(args, format);
    vfwprintf(file, format, args);
    va_end(args);
    fwprintf(file, L"\n");
    fclose(file);
}

bool ReadAll(const std::wstring& path, std::string& out) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER size{};
    bool ok = GetFileSizeEx(file, &size) && size.QuadPart < (1 << 24);
    if (ok) {
        out.resize(static_cast<std::size_t>(size.QuadPart));
        DWORD got = 0;
        ok = out.empty() || (ReadFile(file, out.data(), static_cast<DWORD>(out.size()), &got,
                                      nullptr) && got == out.size());
    }
    CloseHandle(file);
    return ok;
}

DWORD ImageSize(const BYTE* base) {
    auto dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return 0;
    auto nt = reinterpret_cast<const IMAGE_NT_HEADERS32*>(base + dos->e_lfanew);
    return nt->Signature == IMAGE_NT_SIGNATURE ? nt->OptionalHeader.SizeOfImage : 0;
}

void Apply(BYTE* base, DWORD size, const std::wstring& name, const std::string& text) {
    ff13_nccp::Parsed parsed = ff13_nccp::Parse(text);
    int applied = 0;
    for (const auto& patch : parsed.patches) {
        if (!ff13_nccp::Inside(patch, size)) {
            // another game's patch, or another version's: never written past the image
            Log(L"%ls: skipped %llx, outside the game's image", name.c_str(),
                static_cast<unsigned long long>(patch.rva));
            continue;
        }
        BYTE* at = base + patch.rva;
        DWORD was = 0;
        if (!VirtualProtect(at, patch.bytes.size(), PAGE_EXECUTE_READWRITE, &was)) {
            Log(L"%ls: couldn't open %llx for writing (%lu)", name.c_str(),
                static_cast<unsigned long long>(patch.rva), GetLastError());
            continue;
        }
        std::copy(patch.bytes.begin(), patch.bytes.end(), at);
        VirtualProtect(at, patch.bytes.size(), was, &was);
        ++applied;
    }
    FlushInstructionCache(GetCurrentProcess(), base, size);
    Log(L"%ls: %d of %u applied%ls", name.c_str(), applied, static_cast<unsigned>(parsed.patches.size()),
        parsed.skipped.empty() ? L"" : L", some lines didn't read as RVA|bytes");
}

void Run(HMODULE self) {
    wchar_t path[MAX_PATH]{};
    if (!GetModuleFileNameW(self, path, MAX_PATH)) return;
    g_folder = path;
    g_folder.erase(g_folder.find_last_of(L"\\/"));

    auto base = reinterpret_cast<BYTE*>(GetModuleHandleW(nullptr));
    DWORD size = base ? ImageSize(base) : 0;
    if (!size) return;

    std::vector<std::wstring> names;
    WIN32_FIND_DATAW found{};
    HANDLE find = FindFirstFileW((g_folder + L"\\*.nccp").c_str(), &found);
    if (find == INVALID_HANDLE_VALUE) return;
    do {
        if (!(found.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) names.push_back(found.cFileName);
    } while (FindNextFileW(find, &found));
    FindClose(find);
    // NTFS hands them back sorted already, FAT32 and network shares don't
    std::sort(names.begin(), names.end(), [](const std::wstring& a, const std::wstring& b) {
        return _wcsicmp(a.c_str(), b.c_str()) < 0;
    });
    for (const auto& name : names) {
        std::string text;
        if (ReadAll(g_folder + L"\\" + name, text))
            Apply(base, size, name, text);
        else
            Log(L"%ls: couldn't be read", name.c_str());
    }
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Run(module);
    }
    return TRUE;
}
