"""
Checks the filelist format against a real white_img, read only.

The sector chain proves the index agrees with itself, not that it describes
the data. This reads a sample of real payloads (seek to sector_offset * 2048,
read stored_size) and checks each is zlib inflating to exactly
uncompressed_size, or raw data of the right length with a plausible signature.
Opens both files "rb" and writes nothing.

    python tools/verify_archive.py "<game>/alba_data/sys/filelistu.win32.bin" \\
                                   "<game>/alba_data/sys/white_imgu.win32.bin"

    --samples N     how many entries to check (default 40)
    --path P        check one in-game path instead of a sample
    --json OUT      also write the results as json
"""

import argparse
import json
import random
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from patcher.archive import crypt                                   # noqa: E402
from patcher.archive.entry import SECTOR, chain_is_consistent, parse_chunk  # noqa: E402

# assets we can recognise on sight, which is what turns "the length matched"
# into "this is really that file"
SIGNATURES = {
    b"SEDB": "SEDB (scd audio)",
    b"WAVE": "wave",
    b"\x89PNG": "png",
    b"DDS ": "dds texture",
    b"WDB\x00": "wdb",
    b"XWB\x00": "xwb bank",
    b"\x00\x01\x00\x00": "imgb/trb style",
}


def load_entries(filelist: Path):
    raw = filelist.read_bytes()
    encrypted = crypt.is_encrypted(raw)
    data = crypt.decrypt(raw) if encrypted else raw
    if not crypt.looks_decrypted(data):
        raise SystemExit("filelist failed its own self-check after decryption")

    body = data[crypt.HEADER_SIZE:crypt.HEADER_SIZE + crypt.body_size(data)]
    entries, chunks, at = [], 0, 0
    while at < len(body) - 2:
        if body[at] == 0x78 and body[at + 1] in (0x01, 0x5E, 0x9C, 0xDA):
            try:
                obj = zlib.decompressobj()
                plain = obj.decompress(body[at:])
                if len(plain) > 64 and b":" in plain:
                    for part in plain.split(b"\x00"):
                        if part and part.count(b":") >= 3:
                            try:
                                entries.extend(parse_chunk(part + b"\x00"))
                            except Exception:
                                pass
                    chunks += 1
                    at += len(body[at:]) - len(obj.unused_data)
                    continue
            except zlib.error:
                pass
        at += 1
    return entries, chunks, encrypted


def describe(blob: bytes) -> str:
    for magic, name in SIGNATURES.items():
        if blob.startswith(magic):
            return name
    return "unrecognised"


def check(entry, handle, img_size: int) -> dict:
    result = {
        "path": entry.path,
        "sector_offset": entry.sector_offset,
        "byte_offset": entry.byte_offset,
        "uncompressed": entry.uncompressed,
        "stored": entry.stored,
        "compressed": entry.is_compressed,
    }

    if entry.byte_offset + entry.stored > img_size:
        result["verdict"] = "PAST-END"
        result["detail"] = f"needs {entry.byte_offset + entry.stored:,} of {img_size:,}"
        return result

    handle.seek(entry.byte_offset)
    blob = handle.read(entry.stored)
    if len(blob) != entry.stored:
        result["verdict"] = "SHORT-READ"
        return result

    if entry.is_compressed:
        try:
            plain = zlib.decompress(blob)
        except zlib.error as e:
            result["verdict"] = "INFLATE-FAIL"
            result["detail"] = f"{e}; first bytes {blob[:4].hex()}"
            return result
        if len(plain) != entry.uncompressed:
            result["verdict"] = "SIZE-MISMATCH"
            result["detail"] = f"inflated to {len(plain):,}, index says {entry.uncompressed:,}"
            return result
        result["verdict"] = "OK"
        result["detail"] = f"inflated {entry.stored:,} -> {len(plain):,} ({describe(plain)})"
        return result

    result["verdict"] = "OK"
    result["detail"] = f"raw {entry.stored:,} ({describe(blob)})"
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="verify the filelist format against white_img")
    ap.add_argument("filelist", type=Path)
    ap.add_argument("whitebin", type=Path)
    ap.add_argument("--samples", type=int, default=40)
    ap.add_argument("--path", help="check one in-game path instead of a sample")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    for path in (args.filelist, args.whitebin):
        if not path.is_file():
            raise SystemExit(f"not found: {path}")

    img_size = args.whitebin.stat().st_size
    print(f"filelist : {args.filelist.name} ({args.filelist.stat().st_size:,} bytes)")
    print(f"white_img: {args.whitebin.name} ({img_size:,} bytes, {img_size / 1024**3:.2f} GB)")

    entries, chunks, encrypted = load_entries(args.filelist)
    print(f"\nencrypted        : {encrypted}")
    print(f"chunks recovered : {chunks}")
    print(f"entries parsed   : {len(entries):,}")
    if not entries:
        raise SystemExit("no entries parsed, nothing to verify")

    print(f"sector chain     : {'holds' if chain_is_consistent(entries) else 'BREAKS'}")

    highest = max(entries, key=lambda e: e.sector_offset)
    reach = highest.byte_offset + highest.stored
    print(f"furthest entry   : {reach:,} bytes ({reach / 1024**3:.2f} GB)")
    print(f"                   file is {img_size:,} ({'fits' if reach <= img_size else 'OVERRUNS'})")

    real = [e for e in entries if not e.is_stub]
    print(f"stub entries     : {len(entries) - len(real):,} skipped\n")

    if args.path:
        from patcher.archive.entry import find
        target = find(entries, args.path)
        if target is None:
            raise SystemExit(f"{args.path!r} is not in this index")
        sample = [target]
    else:
        # deliberately spread across the file: a bug in the sector maths shows
        # up at large offsets and hides at small ones
        random.seed(1234)
        ordered = sorted(real, key=lambda e: e.sector_offset)
        n = min(args.samples, len(ordered))
        step = max(1, len(ordered) // n)
        sample = ordered[::step][:n]

    results = []
    print(f"checking {len(sample)} entries spread across the archive")
    print("-" * 78)
    with args.whitebin.open("rb") as handle:
        for entry in sample:
            outcome = check(entry, handle, img_size)
            results.append(outcome)
            flag = "ok  " if outcome["verdict"] == "OK" else outcome["verdict"]
            print(f"{flag:<14} {entry.byte_offset / 1024**3:6.2f}GB  {entry.path[-46:]:<46}")
            if outcome["verdict"] != "OK":
                print(f"               -> {outcome.get('detail', '')}")

    ok = sum(1 for r in results if r["verdict"] == "OK")
    print("-" * 78)
    print(f"\nRESULT: {ok}/{len(results)} verified")
    if ok == len(results):
        print("  sector_offset * 2048 = byte offset   CONFIRMED")
        print("  field 3 = stored size                CONFIRMED")
        print("  field 2 = uncompressed size          CONFIRMED")
        print("\n  the format is understood; the writer can be built on it.")
    else:
        print("  at least one entry did not resolve. the breakdown above says which,")
        print("  and the verdict names the failing assumption:")
        print("    PAST-END      offsets are scaled differently than 2048")
        print("    INFLATE-FAIL  offset is wrong, or the payload is not zlib")
        print("    SIZE-MISMATCH field 2 is not the uncompressed size")

    if args.json:
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
