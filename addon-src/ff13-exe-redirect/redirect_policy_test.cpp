// Tests for the redirect's decision logic. Builds with any host compiler:
//
//     g++ -std=c++20 -Wall -Wextra -o policy_test redirect_policy_test.cpp && ./policy_test
//
// The add-on itself only builds on Windows, but the part that can be wrong
// quietly is all in here.

#include "redirect_policy.h"

#include <cstdio>
#include <string>
#include <utility>
#include <vector>

using namespace ff13_redirect;

namespace {

int g_failures = 0;

void Check(bool condition, const char* what) {
    if (!condition) {
        std::printf("  FAIL  %s\n", what);
        ++g_failures;
    } else {
        std::printf("  ok    %s\n", what);
    }
}

constexpr auto kGame = L"C:\\SteamLibrary\\steamapps\\common\\FINAL FANTASY XIII\\white_data\\prog\\win\\bin";

bool Redirects(const wchar_t* requested, unsigned long access = kGenericRead,
               bool hostIsTarget = true, bool haveCopy = true) {
    return ShouldRedirect(requested, access, hostIsTarget, haveCopy);
}

} // namespace

int main() {
    std::printf("the case it exists for\n");
    Check(Redirects(L"C:\\SteamLibrary\\steamapps\\common\\FINAL FANTASY XIII\\white_data\\prog\\win\\bin\\ffxiiiimg.exe"),
          "a read of the game executable is redirected");

    std::printf("\nwrites must reach the real file\n");
    Check(!Redirects(L"C:\\SteamLibrary\\steamapps\\common\\FINAL FANTASY XIII\\white_data\\prog\\win\\bin\\ffxiiiimg.exe",
                     kGenericWrite),
          "GENERIC_WRITE is not redirected");
    Check(!Redirects(L"C:\\SteamLibrary\\steamapps\\common\\FINAL FANTASY XIII\\white_data\\prog\\win\\bin\\ffxiiiimg.exe",
                     kGenericRead | kGenericWrite),
          "read+write is not redirected");
    Check(!Redirects(L"C:\\SteamLibrary\\steamapps\\common\\FINAL FANTASY XIII\\white_data\\prog\\win\\bin\\ffxiiiimg.exe",
                     kFileWriteData),
          "FILE_WRITE_DATA is not redirected");
    Check(!Redirects(L"C:\\SteamLibrary\\steamapps\\common\\FINAL FANTASY XIII\\white_data\\prog\\win\\bin\\ffxiiiimg.exe",
                     kFileAppendData),
          "FILE_APPEND_DATA is not redirected");
    Check(!Redirects(L"C:\\SteamLibrary\\steamapps\\common\\FINAL FANTASY XIII\\white_data\\prog\\win\\bin\\ffxiiiimg.exe",
                     kGenericAll),
          "GENERIC_ALL is not redirected");

    std::printf("\nscope\n");
    // Deliberately NOT excluded any more. The old version required the
    // resolved directory to equal the game directory, and that is what made it
    // miss the executable itself whenever the DRM used a path form
    // GetFullPathNameW normalises differently. We are inside the game process
    // and it is the game's own name, so matching it is the point. FF13Fix does
    // the same and FF13Fix works.
    Check(Redirects(L"C:\\somewhere\\else\\ffxiiiimg.exe"),
          "the name matches wherever it appears, inside this process");
    Check(!Redirects(L"C:\\SteamLibrary\\steamapps\\common\\FINAL FANTASY XIII\\white_data\\prog\\win\\bin\\steam_api.dll"),
          "other files in the game folder are left alone");
    Check(!Redirects(L"C:\\SteamLibrary\\steamapps\\common\\FINAL FANTASY XIII\\white_data\\prog\\win\\bin\\untouched.exe"),
          "the replacement itself is never redirected");
    Check(!Redirects(L"C:\\SteamLibrary\\steamapps\\common\\FINAL FANTASY XIII\\white_data\\prog\\win\\bin\\ffxiiiimg.exe",
                     kGenericRead, /*hostIsTarget=*/false),
          "a different host process is left alone (XIII-2)");
    Check(!Redirects(L"C:\\SteamLibrary\\steamapps\\common\\FINAL FANTASY XIII\\white_data\\prog\\win\\bin\\ffxiiiimg.exe",
                     kGenericRead, true, /*haveCopy=*/false),
          "without untouched.exe it stays inert");
    Check(!Redirects(L""), "an empty path is refused");

    std::printf("\ncase and separators\n");
    Check(Redirects(L"C:\\SteamLibrary\\steamapps\\common\\FINAL FANTASY XIII\\white_data\\prog\\win\\bin\\FFXIIIIMG.EXE"),
          "the filename match is case insensitive");
    Check(Redirects(L"C:/SteamLibrary/steamapps/common/FINAL FANTASY XIII/white_data/prog/win/bin/ffxiiiimg.exe"),
          "forward slashes match too");

    std::printf("\nthe path forms that used to be missed\n");
    // every one of these is the executable, and every one of them survived
    // GetFullPathNameW looking like something the old directory compare
    // rejected. each rejection was a game that would not start.
    Check(Redirects(L"\\\\?\\C:\\SteamLibrary\\steamapps\\common\\FINAL FANTASY XIII\\white_data\\prog\\win\\bin\\ffxiiiimg.exe"),
          "a \\\\?\\ prefixed path matches");
    Check(Redirects(L"\\??\\C:\\FF13\\bin\\ffxiiiimg.exe"),
          "a native \\??\\ path matches");
    Check(Redirects(L"ffxiiiimg.exe"),
          "a bare filename matches");
    Check(Redirects(L".\\ffxiiiimg.exe"),
          "a relative path matches");
    Check(Redirects(L"C:\\PROGRA~1\\FF13\\BIN\\ffxiiiimg.exe"),
          "a short 8.3 path matches");

    std::printf("\nrewriting keeps the caller's path form\n");
    Check(RedirectedPath(L"\\\\?\\C:\\FF13\\bin\\ffxiiiimg.exe")
              == L"\\\\?\\C:\\FF13\\bin\\untouched.exe",
          "the prefix survives and only the name changes");
    Check(RedirectedPath(L"ffxiiiimg.exe") == L"untouched.exe",
          "a bare name becomes a bare name");
    Check(RedirectedPath(L"C:/FF13/bin/FFXIIIIMG.EXE") == L"C:/FF13/bin/untouched.exe",
          "case and slashes both survive");
    Check(RedirectedPath(L"C:\\FF13\\bin\\steam_api.dll").empty(),
          "a path without the name rewrites to nothing");

    std::printf("\nthe same swap without allocating\n");
    {
        // the dinput8 shim is statically imported, so a DLL that fails to load
        // is a game that will not start. it carries no C++ runtime and works in
        // a caller's buffer instead.
        auto swap = [](const wchar_t* input) {
            static wchar_t buffer[512];
            std::size_t length = 0;
            while (input[length] && length < 511) { buffer[length] = input[length]; ++length; }
            buffer[length] = 0;
            const bool hit = RedirectInPlace(buffer, length);
            return std::pair<bool, std::wstring>(hit, std::wstring(buffer));
        };

        auto absolute = swap(L"C:\\FF13\\bin\\ffxiiiimg.exe");
        Check(absolute.first && absolute.second == L"C:\\FF13\\bin\\untouched.exe",
              "an absolute path is rewritten in place");
        auto prefixed = swap(L"\\\\?\\C:\\FF13\\bin\\FFXIIIIMG.EXE");
        Check(prefixed.first && prefixed.second == L"\\\\?\\C:\\FF13\\bin\\untouched.exe",
              "prefix and case both survive");
        auto miss = swap(L"C:\\FF13\\bin\\steam_api.dll");
        Check(!miss.first && miss.second == L"C:\\FF13\\bin\\steam_api.dll",
              "a miss reports false and leaves the buffer untouched");
        Check(!RedirectInPlace(nullptr, 10), "and a null buffer is refused");
    }

    std::printf("\nfinding the game from our own location\n");
    Check(GameDirectoryFromAddon(
              L"C:\\game\\bin\\addons\\ff13-exe-redirect\\00_ff13_exe_redirect.asi")
              == L"C:\\game\\bin",
          "two levels up from addons/<folder>/");
    Check(GameDirectoryFromAddon(L"00_ff13_exe_redirect.asi").empty(),
          "a bare filename yields nothing rather than a guess");
    Check(GameDirectoryFromAddon(L"C:\\loose\\thing.asi").empty(),
          "a path too shallow to be addons/<folder>/ yields nothing");

    std::printf("\nthe replacement path\n");
    Check(ReplacementPath(L"C:\\game\\bin") == L"C:\\game\\bin\\untouched.exe",
          "built beside the game");
    Check(ReplacementPath(L"C:\\game\\bin\\") == L"C:\\game\\bin\\untouched.exe",
          "no doubled separator when one is already there");

    std::printf("\nreading the 4GB flag off an image\n");
    {
        // a minimal PE header, which is all HasLargeAddressFlag looks at
        auto image = [](unsigned short characteristics, bool signature = true) {
            std::vector<unsigned char> bytes(0x200, 0);
            bytes[0] = 'M';
            bytes[1] = 'Z';
            const unsigned headerAt = 0x80;
            bytes[0x3C] = headerAt & 0xFF;
            bytes[0x3D] = (headerAt >> 8) & 0xFF;
            if (signature) {
                bytes[headerAt] = 'P';
                bytes[headerAt + 1] = 'E';
            }
            bytes[headerAt + 0x16] = characteristics & 0xFF;
            bytes[headerAt + 0x17] = (characteristics >> 8) & 0xFF;
            return bytes;
        };
        const auto patched = image(0x0102 | 0x0020);
        const auto stock = image(0x0102);
        const auto unsigned_ = image(0x0020, false);

        Check(HasLargeAddressFlag(patched.data(), patched.size()),
              "a patched image reports the flag");
        Check(!HasLargeAddressFlag(stock.data(), stock.size()),
              "the stock image does not, so the hook stays off");
        Check(!HasLargeAddressFlag(unsigned_.data(), unsigned_.size()),
              "no PE signature means no answer rather than a wrong one");
        Check(!HasLargeAddressFlag(nullptr, 0x1000), "a null image is false");
        Check(!HasLargeAddressFlag(patched.data(), 4),
              "a size that cannot hold the header is false, not a read past the end");
        Check(!HasLargeAddressFlag(patched.data(), 0x90),
              "an e_lfanew pointing past the buffer is refused");
    }

    std::printf("\n%s\n", g_failures ? "FAILURES" : "all checks passed");
    return g_failures ? 1 : 0;
}
