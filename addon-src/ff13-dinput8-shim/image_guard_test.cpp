// Tests for the mapped-header logic. Builds with any host compiler:
//
//     g++ -std=c++20 -Wall -Wextra -o guard_test image_guard_test.cpp && ./guard_test
//
// One of these offsets is used to write into a running executable's headers, so
// "probably right" is not a standard this can be held to.

#include "image_guard.h"

#include <cstdio>
#include <vector>

using namespace ff13_image;

namespace {

int g_failures = 0;

void Check(bool condition, const char* what) {
    if (!condition) {
        ++g_failures;
        std::printf("  FAIL  %s\n", what);
    } else {
        std::printf("  ok    %s\n", what);
    }
}

// a PE32 header with the pieces these functions read
std::vector<std::uint8_t> Image(std::uint32_t lfanew, std::uint16_t characteristics,
                                std::uint32_t checksum, bool signature = true,
                                std::uint16_t magic = 0x010B) {
    std::vector<std::uint8_t> bytes(lfanew + 0x200, 0);
    bytes[0] = 'M';
    bytes[1] = 'Z';
    for (int byte = 0; byte < 4; ++byte) {
        bytes[0x3C + byte] = static_cast<std::uint8_t>((lfanew >> (byte * 8)) & 0xFF);
    }
    if (signature) {
        bytes[lfanew] = 'P';
        bytes[lfanew + 1] = 'E';
    }
    bytes[lfanew + 24] = static_cast<std::uint8_t>(magic & 0xFF);
    bytes[lfanew + 25] = static_cast<std::uint8_t>(magic >> 8);
    bytes[lfanew + 4 + 18] = static_cast<std::uint8_t>(characteristics & 0xFF);
    bytes[lfanew + 4 + 19] = static_cast<std::uint8_t>(characteristics >> 8);
    for (int byte = 0; byte < 4; ++byte) {
        bytes[lfanew + 24 + 64 + byte] = static_cast<std::uint8_t>((checksum >> (byte * 8)) & 0xFF);
    }
    return bytes;
}

} // namespace

int main() {
    std::printf("the offsets FF13Fix hardcoded, computed\n");
    {
        // FF13's executable has e_lfanew = 0x110, and AutoFix.cpp says
        //   ff13_exe_large_address_aware_flag_address = baseAddr + 0x126;
        //   ff13_exe_checksum_address = (uint32_t*)(baseAddr + 0x168);
        const auto ff13 = Image(0x110, 0x0102, 0);
        const HeaderFields at = Locate(ff13.data(), ff13.size());
        Check(at.valid, "FF13's header parses");
        Check(at.characteristics == 0x126, "characteristics lands on 0x126, same as FF13Fix");
        Check(at.checksum == 0x168, "checksum lands on 0x168, same as FF13Fix");
    }

    std::printf("\nand it is computed, not assumed\n");
    {
        const auto other = Image(0x80, 0x0102, 0);
        const HeaderFields at = Locate(other.data(), other.size());
        Check(at.valid && at.characteristics == 0x80 + 22,
              "a different e_lfanew moves both offsets");
        Check(at.checksum == 0x80 + 88, "including the checksum");
    }

    std::printf("\nrefusing rather than guessing\n");
    Check(!Locate(nullptr, 0x400).valid, "a null image");
    Check(!Locate(reinterpret_cast<const std::uint8_t*>("MZ"), 2).valid, "a truncated one");
    {
        const auto unsigned_ = Image(0x110, 0x0102, 0, false);
        Check(!Locate(unsigned_.data(), unsigned_.size()).valid, "no PE signature");
        const auto pe64 = Image(0x110, 0x0102, 0, true, 0x020B);
        Check(!Locate(pe64.data(), pe64.size()).valid,
              "PE32+, whose CheckSum is somewhere else entirely");
        const auto short_ = Image(0x110, 0x0102, 0);
        Check(!Locate(short_.data(), 0x120).valid,
              "a buffer that ends inside the header we would write to");
    }

    std::printf("\nreading the 4GB flag\n");
    {
        const auto patched = Image(0x110, 0x0102 | kLargeAddressAware, 0);
        const auto stock = Image(0x110, 0x0102, 0);
        Check(HasLargeAddressFlag(patched.data(), patched.size()), "set on a patched image");
        Check(!HasLargeAddressFlag(stock.data(), stock.size()), "clear on the stock one");
        Check(!HasLargeAddressFlag(nullptr, 0x1000), "and false rather than a crash");
    }

    std::printf("\nwhat to copy out of untouched.exe\n");
    {
        // the real point: the values come off the clean file, so whatever it
        // actually holds is what the mapped image ends up saying
        const auto clean = Image(0x110, 0x0102, 0xDEADBEEF);
        const Restore want = FromCleanCopy(clean.data(), clean.size());
        Check(want.valid, "a clean copy yields values");
        Check(want.characteristics == 0x0102, "characteristics taken verbatim");
        Check(want.checksum == 0xDEADBEEF,
              "checksum taken verbatim, not zeroed the way FF13Fix hardcodes");

        const auto dirty = Image(0x110, 0x0102 | kLargeAddressAware, 0);
        Check(!FromCleanCopy(dirty.data(), dirty.size()).valid,
              "a backup that is itself 4GB patched is refused, not copied");
        Check(!FromCleanCopy(nullptr, 0x400).valid, "and so is nothing at all");
    }

    std::printf("\n%s\n", g_failures ? "FAILURES" : "all checks passed");
    return g_failures ? 1 : 0;
}
