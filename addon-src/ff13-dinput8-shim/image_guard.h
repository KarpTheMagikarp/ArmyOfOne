// Where the DRM-relevant PE header fields live, and what to put in them.
//
// FF13Fix's FF13_HandleLargeAddressAwarePatch hardcodes base+0x126 (the LAA
// flag) and base+0x168 (the checksum), which only hold while e_lfanew is
// 0x110. These are computed off e_lfanew instead, and image_guard_test checks
// they still come out as 0x126 and 0x168 for XIII's header.

#pragma once

#include <cstddef>
#include <cstdint>

namespace ff13_image {

inline constexpr std::uint16_t kLargeAddressAware = 0x0020;

struct HeaderFields {
    bool valid = false;
    std::size_t characteristics = 0;   // offset of IMAGE_FILE_HEADER.Characteristics
    std::size_t checksum = 0;          // offset of IMAGE_OPTIONAL_HEADER32.CheckSum
};

inline std::uint32_t ReadU32(const std::uint8_t* at) {
    return static_cast<std::uint32_t>(at[0]) |
           (static_cast<std::uint32_t>(at[1]) << 8) |
           (static_cast<std::uint32_t>(at[2]) << 16) |
           (static_cast<std::uint32_t>(at[3]) << 24);
}

inline std::uint16_t ReadU16(const std::uint8_t* at) {
    return static_cast<std::uint16_t>(at[0] | (at[1] << 8));
}

// Locates both fields in a PE32 image, mapped or on disk.
//
// Refuses rather than guesses. Every caller writes to what this returns, and
// one of them writes into the running executable's headers, so an offset
// derived from a header we could not actually parse is a memory corruption
// with a plausible story attached.
inline HeaderFields Locate(const std::uint8_t* image, std::size_t size) {
    constexpr std::size_t kLfanewAt = 0x3C;
    HeaderFields found;
    if (image == nullptr || size < kLfanewAt + 4) return found;
    if (image[0] != 'M' || image[1] != 'Z') return found;

    const std::uint32_t headerAt = ReadU32(image + kLfanewAt);
    // 24 bytes of signature and COFF header, then the optional header, whose
    // CheckSum sits 64 in. 0x58 past the signature is the furthest we touch.
    if (headerAt < kLfanewAt + 4 || headerAt + 0x5C > size) return found;
    if (image[headerAt] != 'P' || image[headerAt + 1] != 'E' ||
        image[headerAt + 2] != 0 || image[headerAt + 3] != 0) {
        return found;
    }
    // PE32 only. This runs inside a 32-bit game and writes into its headers;
    // a PE32+ optional header puts CheckSum somewhere else entirely.
    if (ReadU16(image + headerAt + 24) != 0x010B) return found;

    found.valid = true;
    found.characteristics = headerAt + 4 + 18;
    found.checksum = headerAt + 24 + 64;
    return found;
}

inline bool HasLargeAddressFlag(const std::uint8_t* image, std::size_t size) {
    const HeaderFields at = Locate(image, size);
    if (!at.valid) return false;
    return (ReadU16(image + at.characteristics) & kLargeAddressAware) != 0;
}

// What the running image's two fields should say, copied off untouched.exe.
// FF13Fix writes a literal zero checksum, which is only right for the exe it
// was written against.
struct Restore {
    bool valid = false;
    std::uint16_t characteristics = 0;
    std::uint32_t checksum = 0;
};

inline Restore FromCleanCopy(const std::uint8_t* clean, std::size_t size) {
    const HeaderFields at = Locate(clean, size);
    Restore want;
    if (!at.valid) return want;
    // a clean copy with the 4GB patch already on it is not a clean copy, and
    // copying its header would leave the mapped image advertising the patch
    if ((ReadU16(clean + at.characteristics) & kLargeAddressAware) != 0) return want;
    want.valid = true;
    want.characteristics = ReadU16(clean + at.characteristics);
    want.checksum = ReadU32(clean + at.checksum);
    return want;
}

} // namespace ff13_image
