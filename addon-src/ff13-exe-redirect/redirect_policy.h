// The redirect's decisions, pure and Win32-free so redirect_policy_test.cpp
// can run them on a host compiler. The Win32 calls fail loudly; these fail
// silently, which is why they're the part that gets tested.

#pragma once

#include <string>
#include <string_view>

namespace ff13_redirect {

// Win32 access mask bits, spelled out so this header needs no windows.h
inline constexpr unsigned long kGenericWrite   = 0x40000000ul;
inline constexpr unsigned long kGenericAll     = 0x10000000ul;
inline constexpr unsigned long kFileWriteData  = 0x00000002ul;
inline constexpr unsigned long kFileAppendData = 0x00000004ul;
inline constexpr unsigned long kGenericRead    = 0x80000000ul;

inline constexpr wchar_t kProtectedName[]   = L"ffxiiiimg.exe";
inline constexpr wchar_t kReplacementName[] = L"untouched.exe";

// Opening the exe for writing must reach the real file, or a 4GB patcher gets
// handed untouched.exe and rewrites the one clean copy. Looks removable, isn't.
inline bool WantsWrite(unsigned long access) {
    return (access & (kGenericWrite | kGenericAll | kFileWriteData |
                      kFileAppendData)) != 0ul;
}

inline wchar_t LowerAscii(wchar_t value) {
    return (value >= L'A' && value <= L'Z')
        ? static_cast<wchar_t>(value - L'A' + L'a') : value;
}

// ASCII-only on purpose: every name compared here is one we chose or one
// Square Enix shipped, and a locale-aware fold would introduce behaviour that
// varies by machine for no benefit.
inline bool SameText(std::wstring_view left, std::wstring_view right) {
    if (left.size() != right.size()) return false;
    for (std::size_t index = 0; index < left.size(); ++index) {
        if (LowerAscii(left[index]) != LowerAscii(right[index])) return false;
    }
    return true;
}

// substr without the throw. The throw path drags libstdc++ in, and the shim
// links -nostdlib++. Clamping is what every caller wants anyway.
inline std::wstring_view Slice(std::wstring_view text, std::size_t at,
                               std::size_t count = std::wstring_view::npos) {
    if (at >= text.size()) return {};
    const std::size_t room = text.size() - at;
    return std::wstring_view(text.data() + at, count < room ? count : room);
}

inline std::wstring_view FileNameOf(std::wstring_view path) {
    const std::size_t separator = path.find_last_of(L"\\/");
    return separator == std::wstring_view::npos
        ? path : Slice(path, separator + 1);
}

inline std::wstring_view DirectoryOf(std::wstring_view path) {
    const std::size_t separator = path.find_last_of(L"\\/");
    return separator == std::wstring_view::npos
        ? std::wstring_view{} : Slice(path, 0, separator);
}

// A trailing separator is not part of the identity of a directory, and paths
// arrive with and without one depending on who built them.
inline std::wstring_view TrimSeparator(std::wstring_view path) {
    while (!path.empty() && (path.back() == L'\\' || path.back() == L'/')) {
        path.remove_suffix(1);
    }
    return path;
}

// \ and / compare equal. A stray forward slash would otherwise make the
// redirect silently not happen.
inline bool SamePathText(std::wstring_view left, std::wstring_view right) {
    if (left.size() != right.size()) return false;
    for (std::size_t index = 0; index < left.size(); ++index) {
        wchar_t a = LowerAscii(left[index]);
        wchar_t b = LowerAscii(right[index]);
        if (a == L'/') a = L'\\';
        if (b == L'/') b = L'\\';
        if (a != b) return false;
    }
    return true;
}

inline bool SameDirectory(std::wstring_view left, std::wstring_view right) {
    return SamePathText(TrimSeparator(left), TrimSeparator(right));
}

// The add-on lives in <game>\addons\<folder>\, so the game is two levels up.
// Returns empty when the path is not that shape rather than guessing.
inline std::wstring GameDirectoryFromAddon(std::wstring_view modulePath) {
    std::wstring_view directory = DirectoryOf(modulePath);
    for (int step = 0; step < 2; ++step) {
        if (directory.empty()) return {};
        directory = DirectoryOf(directory);
    }
    return std::wstring(directory);
}

// Where kProtectedName sits inside a path, or npos. A substring search on the
// raw path, same as FF13Fix's strstr. Resolving and comparing directories
// misses \\?\, \??\, 8.3 and relative forms of the exe, and a miss means
// the DRM reads the patched file and the game won't start.
inline std::size_t FindProtectedName(std::wstring_view path) {
    const std::wstring_view needle(kProtectedName);
    if (path.size() < needle.size()) return std::wstring_view::npos;
    for (std::size_t at = 0; at + needle.size() <= path.size(); ++at) {
        if (SameText(Slice(path, at, needle.size()), needle)) return at;
    }
    return std::wstring_view::npos;
}

// Whether a PE image has IMAGE_FILE_LARGE_ADDRESS_AWARE set. Only arm on a
// patched image: redirecting on an unpatched one hands CEG a file it didn't
// ask for and reads as tampering for no reason. Reads mapped memory, so a
// truncated header has to answer false instead of walking off the page.
inline bool HasLargeAddressFlag(const unsigned char* image, std::size_t size) {
    constexpr std::size_t kLfanewAt = 0x3C;
    constexpr unsigned short kLargeAddressAware = 0x0020;
    if (image == nullptr || size < kLfanewAt + 4) return false;
    if (image[0] != 'M' || image[1] != 'Z') return false;
    const std::size_t headerAt =
        static_cast<std::size_t>(image[kLfanewAt]) |
        (static_cast<std::size_t>(image[kLfanewAt + 1]) << 8) |
        (static_cast<std::size_t>(image[kLfanewAt + 2]) << 16) |
        (static_cast<std::size_t>(image[kLfanewAt + 3]) << 24);
    // 0x18 covers the signature plus the whole COFF header
    if (headerAt < kLfanewAt + 4 || headerAt > size - 0x18) return false;
    if (image[headerAt] != 'P' || image[headerAt + 1] != 'E' ||
        image[headerAt + 2] != 0 || image[headerAt + 3] != 0) {
        return false;
    }
    const unsigned short characteristics =
        static_cast<unsigned short>(image[headerAt + 0x16]) |
        static_cast<unsigned short>(image[headerAt + 0x17] << 8);
    return (characteristics & kLargeAddressAware) != 0;
}

// The whole decision, given the path exactly as CreateFile received it.
//
//   requested     the path passed to CreateFile, unmodified
//   hostIsTarget  whether the process we are inside is the one we protect
//   haveCopy      whether untouched.exe exists beside the game
inline bool ShouldRedirect(std::wstring_view requested,
                           unsigned long access,
                           bool hostIsTarget,
                           bool haveCopy) {
    if (!hostIsTarget) return false;      // XIII-2's CEG does not self-read
    if (!haveCopy) return false;          // nothing to substitute; stay inert
    if (WantsWrite(access)) return false; // writes reach the real file
    if (requested.empty()) return false;
    return FindProtectedName(requested) != std::wstring_view::npos;
}

// The requested path with the exe's name swapped for the backup's. Both names
// are 13 characters, so prefix, drive, relative or UNC all survive untouched.
inline std::wstring RedirectedPath(std::wstring_view requested) {
    const std::size_t at = FindProtectedName(requested);
    if (at == std::wstring_view::npos) return {};
    std::wstring result(requested);
    const std::wstring_view replacement(kReplacementName);
    for (std::size_t index = 0; index < replacement.size(); ++index) {
        result[at + index] = replacement[index];
    }
    return result;
}

// The same swap in the caller's buffer, for the shim, which can't allocate
// before the entry point. False and untouched when the name isn't there.
inline bool RedirectInPlace(wchar_t* path, std::size_t length) {
    if (path == nullptr) return false;
    const std::size_t at = FindProtectedName(std::wstring_view(path, length));
    if (at == std::wstring_view::npos) return false;
    const std::wstring_view replacement(kReplacementName);
    for (std::size_t index = 0; index < replacement.size(); ++index) {
        path[at + index] = replacement[index];
    }
    return true;
}

inline std::wstring ReplacementPath(std::wstring_view gameDirectory) {
    std::wstring result(TrimSeparator(gameDirectory));
    result.push_back(L'\\');
    result.append(kReplacementName);
    return result;
}

} // namespace ff13_redirect
