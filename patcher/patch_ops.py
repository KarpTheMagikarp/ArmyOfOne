"""
pure patch operations, bytes in bytes out, no file system touching here on purpose
so we can test the actual patching logic without needing a real game exe around.
file IO and backup handling lives in the engine, not here.
"""

import hashlib
import struct

# from the PE format spec, this is the IMAGE_FILE_LARGE_ADDRESS_AWARE bit
# in the COFF header's Characteristics field
LARGE_ADDRESS_AWARE_FLAG = 0x0020

DOS_HEADER_PE_OFFSET_LOCATION = 0x3C  # e_lfanew, points at the PE signature
PE_SIGNATURE_SIZE = 4  # "PE\0\0"
CHARACTERISTICS_OFFSET_FROM_COFF_HEADER = 18  # 2 bytes wide


def _characteristics_offset(exe_bytes: bytes) -> int:
    """finds where the Characteristics field lives in a PE exe's COFF header"""
    if len(exe_bytes) < DOS_HEADER_PE_OFFSET_LOCATION + 4:
        raise ValueError("file too small to be a valid PE, something's wrong")

    pe_offset = struct.unpack_from("<I", exe_bytes, DOS_HEADER_PE_OFFSET_LOCATION)[0]
    sig = exe_bytes[pe_offset:pe_offset + PE_SIGNATURE_SIZE]
    if sig != b"PE\x00\x00":
        raise ValueError(f"expected PE signature at offset {pe_offset}, got {sig!r}")

    offset = pe_offset + PE_SIGNATURE_SIZE + CHARACTERISTICS_OFFSET_FROM_COFF_HEADER
    if offset + 2 > len(exe_bytes):
        raise ValueError("Characteristics field would read past end of file")
    return offset


def is_laa_enabled(exe_bytes: bytes) -> bool:
    """checks if the large-address-aware flag is already set"""
    offset = _characteristics_offset(exe_bytes)
    characteristics = struct.unpack_from("<H", exe_bytes, offset)[0]
    return bool(characteristics & LARGE_ADDRESS_AWARE_FLAG)


def flip_laa_flag(exe_bytes: bytes) -> bytes:
    """
    sets the LAA flag if it's not already set. this is the actual "4GB patch."
    idempotent, calling it twice on an already-patched exe just returns it unchanged.
    returns a new bytes object, doesn't mutate the input.
    """
    offset = _characteristics_offset(exe_bytes)
    characteristics = struct.unpack_from("<H", exe_bytes, offset)[0]
    patched_characteristics = characteristics | LARGE_ADDRESS_AWARE_FLAG

    patched = bytearray(exe_bytes)
    struct.pack_into("<H", patched, offset, patched_characteristics)
    return bytes(patched)


def clear_laa_flag(exe_bytes: bytes) -> bytes:
    """
    the exact inverse of flip_laa_flag, for uninstalling.

    idempotent the same way: clearing an already-clear flag returns the input
    unchanged. bit-level rather than restoring a backup, because XIII-2 takes
    no backup (it has no DRM check on the exe) and there is nothing to restore
    from.
    """
    offset = _characteristics_offset(exe_bytes)
    characteristics = struct.unpack_from("<H", exe_bytes, offset)[0]
    cleared = characteristics & ~LARGE_ADDRESS_AWARE_FLAG

    patched = bytearray(exe_bytes)
    struct.pack_into("<H", patched, offset, cleared)
    return bytes(patched)


def read_fingerprint(exe_bytes: bytes) -> dict:
    """
    identifies a build from its PE header.

    same fields NovaFix fingerprints on (machine, optional-header magic,
    timestamp, SizeOfImage, .text size), minus their hash of .text, which we
    do not need: SizeOfImage and the .text size already separate every XIII
    build anyone has seen.

    the point is to tell an original Steam executable from a community
    replacement, because that difference decides whether editing it is safe.
    """
    if len(exe_bytes) < 0x40:
        raise ValueError("too small to be a PE")
    pe = struct.unpack_from("<I", exe_bytes, 0x3C)[0]
    if exe_bytes[pe:pe + 4] != b"PE\0\0":
        raise ValueError("no PE signature")

    coff = pe + 4
    machine, sections, timestamp = struct.unpack_from("<HHI", exe_bytes, coff)
    optional_size = struct.unpack_from("<H", exe_bytes, coff + 16)[0]
    optional = coff + 20

    def field(fmt: str, offset: int, size: int):
        # a truncated header reads as zero rather than raising. the caller
        # turns "no match" into "unrecognised build, do not edit", which is
        # the right answer for a file we cannot read properly anyway.
        if offset + size > len(exe_bytes):
            return 0
        return struct.unpack_from(fmt, exe_bytes, offset)[0]

    magic = field("<H", optional, 2)
    # SizeOfImage sits at +56 in a PE32 optional header
    image_size = field("<I", optional + 56, 4)

    text_size = 0
    table = optional + optional_size
    for index in range(sections):
        entry = table + index * 40
        if entry + 40 > len(exe_bytes):
            break
        name = exe_bytes[entry:entry + 8].rstrip(b"\0")
        if name == b".text":
            text_size = field("<I", entry + 8, 4)   # Misc.VirtualSize
            break

    return {"machine": machine, "magic": magic, "timestamp": timestamp,
            "image_size": image_size, "text_size": text_size}


def read_static_imports(exe_bytes: bytes) -> list[str]:
    """
    the DLLs the Windows loader maps before this executable's entry point runs.

    Which is the only list that matters for getting code in front of Steam's
    CEG check. The check lives in the entry point, so a hook has to be armed
    before it, and the loader gives you exactly one free opportunity: the
    DllMain of a statically imported DLL. That is what FF13Fix is, a d3d9.dll
    proxy, and it is why FF13Fix works where a NovaFix add-on cannot.

    A DLL loaded later by LoadLibrary is not in here, and neither is a delay
    import, which is the point: both are too late by definition.

    Returns lowercase names in table order. An unreadable or truncated import
    directory gives an empty list rather than raising, because the caller is a
    diagnostic and "could not tell" is a fine thing for it to print.
    """
    names: list[str] = []
    try:
        pe = struct.unpack_from("<I", exe_bytes, 0x3C)[0]
        if exe_bytes[pe:pe + 4] != b"PE\0\0":
            return []
        coff = pe + 4
        sections = struct.unpack_from("<H", exe_bytes, coff + 2)[0]
        optional_size = struct.unpack_from("<H", exe_bytes, coff + 16)[0]
        optional = coff + 20
        magic = struct.unpack_from("<H", exe_bytes, optional)[0]
        # the data directory starts after the optional header's fixed part,
        # which differs between PE32 and PE32+. import is entry 1.
        directories = optional + (96 if magic == 0x10B else 112)
        import_rva = struct.unpack_from("<I", exe_bytes, directories + 8)[0]
        if not import_rva:
            return []

        table = optional + optional_size
        spans = []
        for index in range(sections):
            entry = table + index * 40
            if entry + 40 > len(exe_bytes):
                break
            virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from(
                "<IIII", exe_bytes, entry + 8)
            spans.append((virtual_address, max(virtual_size, raw_size), raw_pointer))

        def to_offset(rva: int) -> int | None:
            for start, size, raw in spans:
                if start <= rva < start + size:
                    return raw + (rva - start)
            return None

        cursor = to_offset(import_rva)
        if cursor is None:
            return []
        # descriptors are 20 bytes and the table ends at an all-zero one
        while cursor + 20 <= len(exe_bytes):
            name_rva = struct.unpack_from("<I", exe_bytes, cursor + 12)[0]
            if not any(exe_bytes[cursor:cursor + 20]):
                break
            at = to_offset(name_rva) if name_rva else None
            if at is not None and at < len(exe_bytes):
                end = exe_bytes.find(b"\0", at)
                if end > at:
                    names.append(exe_bytes[at:end].decode("ascii", "replace").lower())
            cursor += 20
    except (struct.error, IndexError, ValueError):
        return names
    return names


# DLLs worth proxying: small, stable export sets, and every one of them is a
# system DLL a game imports statically rather than something it ships. d3d9 is
# last on purpose. It is the one FF13Fix proved works, and it is also the one
# NovaFix already occupies, so taking it means a proxy chain rather than a
# standalone shim.
PROXY_CANDIDATES = ("dinput8.dll", "winmm.dll", "dsound.dll", "xinput1_3.dll",
                    "version.dll", "dbghelp.dll", "d3d9.dll")


# XIII builds, from NovaFix's own catalog (src/game/titles/ff13/title_adapter.cpp).
# the distinction matters: the original Steam executable is CEG protected, and
# editing it in place is what trips the DRM. the community replacement has
# already had that dealt with, so the 4GB flag can be flipped safely.
XIII_BUILDS = {
    (38313984, 13185024): {
        "name": "Nova/community replacement executable",
        "safe_to_edit": True,
    },
    (38899712, 13181582): {
        "name": "original Steam CEG executable",
        "safe_to_edit": False,
    },
}


def identify_xiii_build(exe_bytes: bytes) -> dict:
    """
    which XIII build this is, and whether it can be edited.

    an unknown build is treated as unsafe. a new Steam update would land here,
    and guessing "probably fine" on an executable we do not recognise is how a
    user ends up with a game that will not start.
    """
    try:
        fingerprint = read_fingerprint(exe_bytes)
    except ValueError as e:
        return {"known": False, "safe_to_edit": False, "name": f"unreadable ({e})"}

    build = XIII_BUILDS.get((fingerprint["image_size"], fingerprint["text_size"]))
    if build is None:
        return {"known": False, "safe_to_edit": False,
                "name": "unrecognised build", **fingerprint}
    return {"known": True, **build, **fingerprint}


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_checksum(data: bytes, expected_hex: str) -> bool:
    return sha256_of(data) == expected_hex.lower()


# maps the "operation" string used in a manifest to the real function that
# does it. only add something here once it's actually implemented, that
# way the engine can tell a real op apart from a placeholder id that
# hasn't been built yet.
OPERATIONS = {
    "flip_laa_flag": flip_laa_flag,
}

# maps that same operation string to a function that checks whether it's
# already been applied by inspecting the actual bytes. used for scanning
# real on-disk state instead of trusting a recorded log blindly. not
# every operation needs one of these to exist as OPERATIONS.
CHECKS = {
    "flip_laa_flag": is_laa_enabled,
}

# How much of a file a check actually needs. Every check here reads the PE
# header and nothing else, and the header is in the first page.
#
# This is not a micro-optimisation. The scan runs on every game switch, and
# reading the whole of XIII's 38MB executable to look at one bit in its header
# is most of the delay between opening a game and the rows knowing what they
# are. The user sees that delay as rows that say REQUIRED and then change their
# mind.
#
# A check that raises on a short read is retried with the whole file, so a
# build with a header further in than this degrades to correct-but-slow rather
# than wrong.
CHECK_PREFIX = 64 * 1024
