// The 4GB patch's other half, as a dinput8.dll proxy.
//
// XIII's CEG check reads its own exe from the entry point, so whatever hides
// the patch has to be loaded before that. The loader runs statically imported
// DLLs first, and ffxiiiimg.exe imports dinput8 (NovaFix already has d3d9). A
// NovaFix add-on loads at render init, which is too late, and a refused
// process never gets there.
//
// Same two moves as FF13Fix: hook CreateFileA/W so the check's open of
// ffxiiiimg.exe gets untouched.exe (then unhook), and copy untouched.exe's PE
// header fields onto the running image, since the check compares memory
// against the file. The address space was sized at CreateProcess, so the 4GB
// stays.
//
// No C runtime on purpose. A static import that fails to load is a game that
// doesn't start, so this only imports kernel32. The decisions live in two
// headers a host compiler tests. Full story in docs/4gb-patch.md.

#include "../ff13-exe-redirect/redirect_policy.h"
#include "image_guard.h"

#include <windows.h>

#include <MinHook.h>

namespace {

using namespace ff13_redirect;

using CreateFileWFn = HANDLE(WINAPI*)(LPCWSTR, DWORD, DWORD,
    LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
using CreateFileAFn = HANDLE(WINAPI*)(LPCSTR, DWORD, DWORD,
    LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);

CreateFileWFn g_realCreateFileW = nullptr;
CreateFileAFn g_realCreateFileA = nullptr;

constexpr std::size_t kPathMax = 1024;

// kPathMax, NOT MAX_PATH. This was MAX_PATH * 2 while Arm() bounded the copy
// into it against kPathMax, which is nearly twice as big: a game folder over
// 519 characters wrote past the end of it, inside DllMain, before the game had
// run a single instruction. longPathAware installs and deep Steam libraries
// make that reachable rather than theoretical.
wchar_t g_gameDirectory[kPathMax]{};
bool g_armed = false;

std::size_t Length(const wchar_t* text) {
    std::size_t count = 0;
    while (text && text[count]) ++count;
    return count;
}

void Append(wchar_t* into, std::size_t room, const wchar_t* text) {
    std::size_t at = Length(into);
    while (text && *text && at + 1 < room) into[at++] = *text++;
    into[at] = 0;
}

// A log beside the game, written with WriteFile because a proxy in somebody
// else's process should not be dragging in stdio.
//
// This file existing at all is the diagnostic that matters. The add-on version
// of this never wrote one, and that turned out to be the whole answer: it had
// not been loaded. So the first line goes down before anything can decide to
// stay inert.
void Log(const wchar_t* message, const wchar_t* detail = nullptr) {
    if (!g_gameDirectory[0]) return;
    wchar_t path[kPathMax]{};
    Append(path, kPathMax, g_gameDirectory);
    Append(path, kPathMax, L"\\ff13-dinput8-shim.log");

    HANDLE file = CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ, nullptr,
                              OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return;

    wchar_t line[kPathMax * 2]{};
    Append(line, kPathMax * 2, message);
    if (detail) {
        Append(line, kPathMax * 2, L" ");
        Append(line, kPathMax * 2, detail);
    }
    Append(line, kPathMax * 2, L"\r\n");

    // utf-8 so the file opens in anything, and so a path with non-ascii in it
    // arrives readable in a bug report
    // three bytes per BMP character in the worst case, so this is sized off the
    // wide buffer rather than guessed. WideCharToMultiByte refuses rather than
    // overflowing either way, but refusing means a dropped diagnostic line.
    char narrow[kPathMax * 2 * 3 + 1]{};
    const int bytes = WideCharToMultiByte(CP_UTF8, 0, line, -1, narrow,
                                          static_cast<int>(sizeof(narrow)), nullptr, nullptr);
    if (bytes > 1) {
        DWORD written = 0;
        WriteFile(file, narrow, static_cast<DWORD>(bytes - 1), &written, nullptr);
    }
    CloseHandle(file);
}

// Once the DRM's read is served, the hook has done its whole job.
//
// FF13Fix disables both hooks the moment one of them matches, and that is not
// an optimisation. An inline hook on CreateFile sits in front of every file the
// game opens for as long as it runs, and the one read it exists for happens
// during startup. Leaving it armed buys nothing and costs every asset load.
void Disarm() {
    if (!g_armed) return;
    g_armed = false;
    MH_DisableHook(MH_ALL_HOOKS);
}

HANDLE WINAPI CreateFileWHook(LPCWSTR fileName, DWORD access, DWORD share,
                              LPSECURITY_ATTRIBUTES security, DWORD creation,
                              DWORD flags, HANDLE templateFile) {
    if (g_armed && fileName) {
        const std::size_t length = Length(fileName);
        if (length && length < kPathMax &&
            ShouldRedirect(std::wstring_view(fileName, length), access, true, true)) {
            wchar_t swapped[kPathMax]{};
            for (std::size_t index = 0; index < length; ++index) swapped[index] = fileName[index];
            if (RedirectInPlace(swapped, length)) {
                // AFTER the substitute is confirmed, not before. Disarming on
                // the way in means a missing untouched.exe costs the redirect
                // for the rest of the process, and the one read this exists
                // for may not have happened yet.
                if (GetFileAttributesW(swapped) != INVALID_FILE_ATTRIBUTES) {
                    Disarm();
                    Log(L"served untouched.exe for", fileName);
                    return g_realCreateFileW(swapped, access, share, security,
                                             creation, flags, templateFile);
                }
                Log(L"no untouched.exe beside", swapped);
            }
        }
    }
    return g_realCreateFileW(fileName, access, share, security, creation, flags,
                             templateFile);
}

HANDLE WINAPI CreateFileAHook(LPCSTR fileName, DWORD access, DWORD share,
                              LPSECURITY_ATTRIBUTES security, DWORD creation,
                              DWORD flags, HANDLE templateFile) {
    if (g_armed && fileName) {
        wchar_t wide[kPathMax]{};
        const int converted = MultiByteToWideChar(CP_ACP, 0, fileName, -1, wide,
                                                  static_cast<int>(kPathMax));
        if (converted > 1) {
            const std::size_t length = static_cast<std::size_t>(converted) - 1;
            // kept because RedirectInPlace rewrites in place, and the log is
            // worthless if it reports the destination as the request. the real
            // one said "served untouched.exe for ...\untouched.exe".
            wchar_t asked[kPathMax]{};
            for (std::size_t index = 0; index <= length; ++index) asked[index] = wide[index];
            if (ShouldRedirect(std::wstring_view(wide, length), access, true, true) &&
                RedirectInPlace(wide, length)) {
                if (GetFileAttributesW(wide) != INVALID_FILE_ATTRIBUTES) {
                    char narrow[kPathMax * 2]{};
                    const int back = WideCharToMultiByte(CP_ACP, 0, wide, -1, narrow,
                                                         static_cast<int>(sizeof(narrow)),
                                                         nullptr, nullptr);
                    if (back > 1) {
                        Disarm();
                        Log(L"served untouched.exe for (ansi)", asked);
                        return g_realCreateFileA(narrow, access, share, security,
                                                 creation, flags, templateFile);
                    }
                }
            }
        }
    }
    return g_realCreateFileA(fileName, access, share, security, creation, flags,
                             templateFile);
}

bool ReadHead(const wchar_t* path, unsigned char* into, DWORD room, DWORD* got) {
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr,
                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    const BOOL ok = ReadFile(file, into, room, got, nullptr);
    CloseHandle(file);
    return ok == TRUE;
}

// Make the mapped headers say what untouched.exe says.
//
// Header pages are read-only in a mapped image, so this goes through
// VirtualProtect and puts the protection back. Failing here is not fatal on
// its own, and it is logged rather than swallowed: if the DRM turns out to
// compare only the file, the redirect alone is enough, and if it compares
// memory too then this line in the log is the first place to look.
bool RestoreMappedHeaders(unsigned char* image, const wchar_t* cleanPath) {
    unsigned char head[0x400]{};
    DWORD got = 0;
    if (!ReadHead(cleanPath, head, sizeof(head), &got) || got < 0x200) {
        Log(L"could not read the clean copy's header");
        return false;
    }
    const ff13_image::Restore want = ff13_image::FromCleanCopy(head, got);
    const ff13_image::HeaderFields live = ff13_image::Locate(image, 0x1000);
    if (!want.valid || !live.valid) {
        Log(L"header layout not recognised, leaving the image alone");
        return false;
    }

    DWORD previous = 0;
    if (!VirtualProtect(image, 0x1000, PAGE_READWRITE, &previous)) {
        Log(L"could not unprotect the mapped headers");
        return false;
    }
    auto* characteristics = reinterpret_cast<std::uint16_t*>(image + live.characteristics);
    auto* checksum = reinterpret_cast<std::uint32_t*>(image + live.checksum);
    *characteristics = want.characteristics;
    *checksum = want.checksum;
    VirtualProtect(image, 0x1000, previous, &previous);
    Log(L"mapped headers restored from untouched.exe");
    return true;
}

void Arm(HMODULE self) {
    wchar_t modulePath[kPathMax]{};
    if (!GetModuleFileNameW(self, modulePath, kPathMax)) return;
    // we sit beside the executable, so one level up is the game folder
    const std::wstring_view directory = DirectoryOf(std::wstring_view(modulePath));
    if (directory.empty() || directory.size() + 1 >= kPathMax) return;
    for (std::size_t index = 0; index < directory.size(); ++index) {
        g_gameDirectory[index] = directory[index];
    }
    g_gameDirectory[directory.size()] = 0;

    // FIRST LINE BEFORE ANY DECISION. The add-on this replaces wrote nothing on
    // a failed launch and it took days to work out that meant "never loaded"
    // rather than "stayed quiet". Now the file's existence answers that.
    Log(L"loaded into", modulePath);

    wchar_t hostPath[kPathMax]{};
    if (!GetModuleFileNameW(nullptr, hostPath, kPathMax)) return;
    if (!SameText(FileNameOf(std::wstring_view(hostPath)), kProtectedName)) {
        Log(L"inactive: host is not the protected executable", hostPath);
        return;
    }

    auto* image = reinterpret_cast<unsigned char*>(GetModuleHandleW(nullptr));
    if (!ff13_image::HasLargeAddressFlag(image, 0x1000)) {
        Log(L"inactive: not 4GB patched, nothing to hide");
        return;
    }

    wchar_t clean[kPathMax]{};
    Append(clean, kPathMax, g_gameDirectory);
    Append(clean, kPathMax, L"\\");
    Append(clean, kPathMax, kReplacementName);
    if (GetFileAttributesW(clean) == INVALID_FILE_ATTRIBUTES) {
        Log(L"inactive: no untouched.exe beside the game");
        return;
    }

    // headers first. it needs no hook, it cannot fail halfway, and doing it
    // before arming means the two are never briefly out of step if the read
    // lands early.
    RestoreMappedHeaders(image, clean);

    if (MH_Initialize() != MH_OK) {
        Log(L"MinHook failed to initialise");
        return;
    }
    const bool wide = MH_CreateHook(reinterpret_cast<LPVOID>(&CreateFileW),
                                    reinterpret_cast<LPVOID>(&CreateFileWHook),
                                    reinterpret_cast<LPVOID*>(&g_realCreateFileW)) == MH_OK;
    const bool ansi = MH_CreateHook(reinterpret_cast<LPVOID>(&CreateFileA),
                                    reinterpret_cast<LPVOID>(&CreateFileAHook),
                                    reinterpret_cast<LPVOID*>(&g_realCreateFileA)) == MH_OK;
    if (!wide || !ansi || MH_EnableHook(MH_ALL_HOOKS) != MH_OK) {
        Log(L"hook install failed");
        MH_Uninitialize();
        return;
    }
    g_armed = true;
    Log(L"armed: the next read of ffxiiiimg.exe gets untouched.exe");
}

// The real dinput8, resolved from the system directory and never from beside
// the game, which is where this file is.
//
// Loaded on first call rather than at attach. LoadLibrary inside DllMain runs
// under the loader lock and is the classic way to deadlock a process on
// startup; every export below is called long after the loader has let go.
HMODULE RealDinput8() {
    static HMODULE real = nullptr;
    if (real) return real;
    wchar_t path[kPathMax]{};
    const UINT length = GetSystemDirectoryW(path, kPathMax);
    if (!length || length >= kPathMax) return nullptr;
    Append(path, kPathMax, L"\\dinput8.dll");
    real = LoadLibraryW(path);
    if (!real) Log(L"could not load the system dinput8", path);
    return real;
}

// GetProcAddress hands back FARPROC, which is int(__stdcall*)(), and casting
// that straight to a real signature is what -Wcast-function-type exists to
// complain about. Going through void* is the sanctioned spelling and it is
// also the honest one: the loader gave us an address, the signature is ours to
// assert, and asserting it in one place beats five reinterpret_casts.
template <typename Fn>
Fn Forward(const char* name) {
    HMODULE real = RealDinput8();
    if (!real) return nullptr;
    return reinterpret_cast<Fn>(reinterpret_cast<void*>(GetProcAddress(real, name)));
}

} // namespace

extern "C" {

HRESULT WINAPI DirectInput8Create(HINSTANCE instance, DWORD version,
                                                        REFIID iid, LPVOID* out,
                                                        LPUNKNOWN outer) {
    using Fn = HRESULT(WINAPI*)(HINSTANCE, DWORD, REFIID, LPVOID*, LPUNKNOWN);
    auto real = Forward<Fn>("DirectInput8Create");
    if (!real) return E_FAIL;
    return real(instance, version, iid, out, outer);
}

HRESULT WINAPI DllCanUnloadNow() {
    using Fn = HRESULT(WINAPI*)();
    auto real = Forward<Fn>("DllCanUnloadNow");
    // S_FALSE means "still in use", which is the answer that keeps a caller
    // from unloading a dinput8 it cannot reach
    return real ? real() : S_FALSE;
}

HRESULT WINAPI DllGetClassObject(REFCLSID clsid, REFIID iid, LPVOID* out) {
    using Fn = HRESULT(WINAPI*)(REFCLSID, REFIID, LPVOID*);
    auto real = Forward<Fn>("DllGetClassObject");
    if (!real) return CLASS_E_CLASSNOTAVAILABLE;
    return real(clsid, iid, out);
}

HRESULT WINAPI DllRegisterServer() {
    using Fn = HRESULT(WINAPI*)();
    auto real = Forward<Fn>("DllRegisterServer");
    return real ? real() : E_FAIL;
}

HRESULT WINAPI DllUnregisterServer() {
    using Fn = HRESULT(WINAPI*)();
    auto real = Forward<Fn>("DllUnregisterServer");
    return real ? real() : E_FAIL;
}

} // extern "C"

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID reserved) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Arm(module);
    } else if (reason == DLL_PROCESS_DETACH && reserved == nullptr) {
        // reserved is null for FreeLibrary and non-null for process shutdown.
        // On shutdown the other threads are already gone and MinHook's disable
        // enumerates them from inside the loader lock, which is a way to hang
        // on exit for no benefit at all: the process is leaving and the hook
        // leaves with it. On a real FreeLibrary the hook has to come out, or
        // it points into unmapped memory.
        Disarm();
        MH_Uninitialize();
    }
    return TRUE;
}
