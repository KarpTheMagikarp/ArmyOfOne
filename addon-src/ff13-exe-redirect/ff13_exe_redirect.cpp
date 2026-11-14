// FF13 exe redirect as a NovaFix add-on. Not installed any more.
//
// It hooks CreateFileA/W so XIII's CEG check reads untouched.exe while the 4GB
// patched image runs, which is FF13Fix's trick (d3d9ex/AutoFix.cpp). The hook
// works, but NovaFix loads add-ons at render init and the check runs in the
// entry point, so it never got a chance. The dinput8 shim replaced it and
// shares redirect_policy.h. Kept so the engine can still find and remove old
// installs. See docs/4gb-patch.md.

#include "redirect_policy.h"

#include <windows.h>

#include <MinHook.h>

#include <cstdio>
#include <cwchar>
#include <string>

namespace {

using namespace ff13_redirect;

using CreateFileWFn = HANDLE(WINAPI*)(LPCWSTR, DWORD, DWORD,
    LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
using CreateFileAFn = HANDLE(WINAPI*)(LPCSTR, DWORD, DWORD,
    LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);

CreateFileWFn g_originalCreateFileW = nullptr;
CreateFileAFn g_originalCreateFileA = nullptr;

std::wstring g_gameDirectory;      // where our own module lives
std::wstring g_replacementPath;    // ...\untouched.exe, resolved once
bool g_active = false;

void Log(const wchar_t* format, ...) {
    // a file beside the game, because a add-on has no console and a silent
    // failure here is indistinguishable from a working redirect
    wchar_t path[MAX_PATH]{};
    if (g_gameDirectory.empty()) return;
    _snwprintf_s(path, _TRUNCATE, L"%s\\ff13-exe-redirect.log",
                 g_gameDirectory.c_str());
    FILE* file = nullptr;
    if (_wfopen_s(&file, path, L"a, ccs=UTF-8") != 0 || !file) return;
    va_list args;
    va_start(args, format);
    vfwprintf(file, format, args);
    va_end(args);
    fwprintf(file, L"\n");
    fclose(file);
}

// The replacement path, or empty to leave the call alone. The name is swapped
// in place, never resolved: GetFullPathNameW mangles \\?\, 8.3 and relative
// paths into something that no longer looks like the game dir, and then the
// DRM silently reads the patched exe. A missing untouched.exe means hands off,
// not failing every open of the exe.
std::wstring Redirect(const wchar_t* requested, DWORD access) {
    if (!g_active || !requested) return {};
    const std::wstring_view raw(requested);
    if (!ShouldRedirect(raw, access, /*hostIsTarget=*/true, /*haveCopy=*/true)) {
        return {};
    }
    std::wstring swapped = RedirectedPath(raw);
    if (swapped.empty()) return {};
    if (GetFileAttributesW(swapped.c_str()) == INVALID_FILE_ATTRIBUTES) {
        // the caller's path form did not resolve for the backup. fall back to
        // the absolute one we worked out at load, which is known to exist.
        return g_replacementPath;
    }
    return swapped;
}

HANDLE WINAPI CreateFileWHook(LPCWSTR fileName, DWORD access, DWORD share,
                              LPSECURITY_ATTRIBUTES security, DWORD creation,
                              DWORD flags, HANDLE templateFile) {
    const std::wstring replacement = Redirect(fileName, access);
    if (!replacement.empty()) {
        Log(L"redirect W: %s -> %s", fileName, replacement.c_str());
        return g_originalCreateFileW(replacement.c_str(), access, share,
                                     security, creation, flags, templateFile);
    }
    return g_originalCreateFileW(fileName, access, share, security, creation,
                                 flags, templateFile);
}

HANDLE WINAPI CreateFileAHook(LPCSTR fileName, DWORD access, DWORD share,
                              LPSECURITY_ATTRIBUTES security, DWORD creation,
                              DWORD flags, HANDLE templateFile) {
    if (fileName) {
        wchar_t wide[MAX_PATH * 2]{};
        const int converted = MultiByteToWideChar(
            CP_ACP, 0, fileName, -1, wide, static_cast<int>(std::size(wide)));
        if (converted > 0) {
            const std::wstring replacement = Redirect(wide, access);
            if (!replacement.empty()) {
                char narrow[MAX_PATH * 2]{};
                const int back = WideCharToMultiByte(
                    CP_ACP, 0, replacement.c_str(), -1, narrow,
                    static_cast<int>(std::size(narrow)), nullptr, nullptr);
                if (back > 0) {
                    Log(L"redirect A: %S -> %s", fileName, replacement.c_str());
                    return g_originalCreateFileA(narrow, access, share, security,
                                                 creation, flags, templateFile);
                }
            }
        }
    }
    return g_originalCreateFileA(fileName, access, share, security, creation,
                                 flags, templateFile);
}

// Decides whether to do anything at all, once, at load. Every answer here is
// "leave the process exactly as it would have been".
bool ShouldActivate(HMODULE self) {
    wchar_t modulePath[MAX_PATH * 2]{};
    if (!GetModuleFileNameW(self, modulePath, static_cast<DWORD>(std::size(modulePath)))) {
        return false;
    }
    // we live in <game>\addons\<our folder>\, so the game directory is two up
    std::wstring directory = GameDirectoryFromAddon(modulePath);
    if (directory.empty()) return false;
    g_gameDirectory = directory;

    // only the title that needs it. XIII-2's CEG does not read its own
    // executable, which is why FF13Fix never asked for an untouched.exe there.
    wchar_t hostPath[MAX_PATH * 2]{};
    if (!GetModuleFileNameW(nullptr, hostPath, static_cast<DWORD>(std::size(hostPath)))) {
        return false;
    }
    // hostIsTarget: only the title whose CEG reads its own executable
    if (!SameText(FileNameOf(hostPath), kProtectedName)) {
        return false;
    }
    if (!SameDirectory(DirectoryOf(hostPath), g_gameDirectory)) {
        return false;
    }

    const std::wstring replacement = ReplacementPath(g_gameDirectory);
    if (GetFileAttributesW(replacement.c_str()) == INVALID_FILE_ATTRIBUTES) {
        // nothing to substitute. staying inert leaves the user exactly where
        // they were rather than failing every open of the executable.
        Log(L"inactive: no %s beside the game", kReplacementName);
        return false;
    }

    // only arm on a patched image. untouched.exe outlives the patch on
    // purpose, so its presence says nothing; the header flag does.
    const auto* image = reinterpret_cast<const unsigned char*>(GetModuleHandleW(nullptr));
    if (!HasLargeAddressFlag(image, 0x1000)) {
        Log(L"inactive: %s is not 4GB patched, nothing to hide", kProtectedName);
        return false;
    }

    g_replacementPath = replacement;
    return true;
}

void Install(HMODULE self) {
    if (!ShouldActivate(self)) return;

    if (MH_Initialize() != MH_OK) {
        Log(L"MinHook failed to initialise");
        return;
    }
    const bool wide = MH_CreateHook(
        reinterpret_cast<LPVOID>(&CreateFileW),
        reinterpret_cast<LPVOID>(&CreateFileWHook),
        reinterpret_cast<LPVOID*>(&g_originalCreateFileW)) == MH_OK;
    const bool ansi = MH_CreateHook(
        reinterpret_cast<LPVOID>(&CreateFileA),
        reinterpret_cast<LPVOID>(&CreateFileAHook),
        reinterpret_cast<LPVOID*>(&g_originalCreateFileA)) == MH_OK;

    if (!wide || !ansi || MH_EnableHook(MH_ALL_HOOKS) != MH_OK) {
        Log(L"hook install failed (W=%d A=%d)", wide ? 1 : 0, ansi ? 1 : 0);
        MH_Uninitialize();
        return;
    }

    g_active = true;
    Log(L"active: %s reads will be served from %s", kProtectedName,
        g_replacementPath.c_str());
}

} // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Install(module);
    } else if (reason == DLL_PROCESS_DETACH && g_active) {
        g_active = false;
        MH_DisableHook(MH_ALL_HOOKS);
        MH_Uninitialize();
    }
    return TRUE;
}
