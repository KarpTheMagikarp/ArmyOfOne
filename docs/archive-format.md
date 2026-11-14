# Archive format: filelist and white_img

The games keep almost everything in archive pairs: an index and a data file.

```
filelistu.win32.bin    index: paths, offsets, sizes, in zlib chunks
white_imgu.win32.bin   data: every file concatenated, no names
```

`u` is English, `c` Japanese (LR: `a` and `v`). They live in `white_data/sys`
(XIII), `alba_data/sys` (XIII-2) and `weiss_data` (LR). A `white_img` runs
from about 1.5 GB to 12 GB.

Reference: Surihix's [WhiteBinTools](https://github.com/Surihix/WhiteBinTools)
and [WhiteCryptTool](https://github.com/Surihix/WhiteCryptTool). Everything
below was checked against real game files.

## Modules

```
patcher/archive/crypt.py        XIII-2 / LR filelist encryption (WhiteCryptTool port, GPL-3.0)
patcher/archive/entry.py        chunk entry text, parse and serialise (no IO)
patcher/archive/filelist.py     the index body: chunks, records, rebuild
patcher/archive/spanbackup.py   journal of overwritten byte spans
patcher/archive/inject.py       in-place replace within an entry's span
patcher/archive/grow.py         standalone append-and-repoint (the drawer does the same inline)
patcher/archive/transaction.py  one operation across every language archive
```

## Layers

```
filelist*.win32.bin
  -> decrypt            (XIII-2 and LR; XIII's are in the clear)
    -> body: header, 8-byte records, chunk table, zlib chunks
      -> chunk text: NUL-separated entries
```

### Encryption

```
[0..32)                 header, never encrypted
[32..32+body_size)      encrypted body, 8-byte blocks
[32+body_size..end)     trailing data, never encrypted
```

`body_size` is a big-endian uint32 at offset 16, plus 8. The key schedule's
seed is four header bytes, so no external key. A 264-byte table comes from the
seed; each block XOR-chains against its predecessor byte, goes through a
substitution, and is offset by a value from the block index. Decrypt then
encrypt is byte identical, and the body checksum we compute matches the one
in the file. Encrypted, a real `filelistu` measures 7.999 bits/byte with no
readable strings.

XIII's `filelist_scr*` pair is in the clear and also lacks the length and
checksum that `looks_decrypted()` keys on, so `filelist._layouts` tries
candidate shapes and only accepts one where every chunk inflates to its
declared length.

### Body

```
+0     uint32   offset of the chunk table
+4     uint32   offset of the chunk data
+8     uint32   entry count
+12             entry records, 8 bytes each
table           12 bytes per chunk: (plain length, deflated length, offset)
chunks          contiguous zlib streams, level 9, no gaps
padding         to an 8 byte boundary (the cipher's block)
tail -8         uint32 body length
tail -4         uint32 checksum
```

The sealed container (XIII-2, LR) wraps this in a 32-byte header with the
length at 16 and a trailing length and checksum. XIII's bare body ends on the
last chunk's last byte. `PLAIN_32` is only a shape `parse()` tries; writing one
refuses, since no real file has it.

### Entry records

XIII-2 and LR:

```
+0    uint32   file code, the key the game looks a file up by
+4    uint24   position, little endian
+7    uint8    flags
```

Position is `chunk_index * 0x8000 + offset of the entry's text in that chunk`,
so each chunk owns a fixed 32KB slice. Records are sorted by file code, not
chunk. Checked on the 14,381-entry XIII-2 index: every record maps onto a real
text boundary, one each.

XIII: a uint16 chunk at +4 and a uint16 offset at +6, no stride. Checked on
the real `filelist_scru`, 3,076 records onto 3,076 boundaries.

### Chunk text

```
<sector_offset>:<uncompressed_size>:<stored_size>:<path>\0
0:4e9a68:27052f:chr/pc/c001/bin/c001.win32.imgb
```

Lowercase hex, no `0x`. `sector_offset` is in 2048-byte sectors. Each entry
starts at the sector after the previous one ends,
`ceil((offset * 2048 + stored) / 2048)`, which holds for all 14,352
consecutive pairs in the XIII-2 index. WhiteBinTools' dumps add a `code|`
prefix; that's its text format, not the archive's.

`tools/verify_archive.py` reads a sample of real payloads back by these
numbers (zlib inflating to exactly `uncompressed_size`, or raw data of the
right length), read only.

## Writing an index

- **Original deflate bytes are kept** for every chunk that wasn't edited.
  zlib output differs between builds (about ten bytes a chunk on one user's
  Python), so re-deflating everything would make byte identity depend on the
  machine. An unedited rebuild is byte identical anywhere; a test swaps the
  compression level to prove it.
- **The chunk table and offsets are always rebuilt**, since an edit changes a
  chunk's deflated length.
- **`verify_roundtrip()`** parses the user's own index, rebuilds it unedited
  and requires byte identity before any write. It compares against that file,
  not vanilla, so a modded index works.
- `replace_entry()` edits a record in place and refuses a text length change.
  `relocate_entry()` rewrites the lookup records so an entry can be any size,
  shifting later records in the same chunk and checking the 0x8000 stride
  (the XIII-2 sample's fullest chunk is 8,250 bytes). Every occurrence of a
  path moves: 274 paths in the XIII-2 sample are listed more than once.

## Writing white_img

Two ways in, both streaming (nothing loads a container into memory):

- **In place** (`inject.py`). An entry owns its stored bytes plus padding to
  the next sector, so a replacement that fits the span goes in without moving
  anything. Anything bigger is refused here.
- **Append** (`grow.py`). The replacement goes on the end on a sector boundary
  and only that entry's offset and sizes change. The old bytes are left in
  place, so undo is truncate to the original length and put the index back.
  Reinstalling appends again; revert reclaims it all. Drawer mods
  (`mods/game_files.py`) and the HD voices copy (`mods/hd_mirror.py`) append
  this way through `filelist.relocate_many`; `grow.py` is the standalone form,
  which the tests also use to stand in for another tool's appends.

Order for both:

1. resolve and validate everything, touching nothing
2. record what's about to change (span journal fsynced, or index copied and
   white_img length noted)
3. write the payload
4. write the rewritten index
5. read back and check every entry lands where it should
6. drop the journal only once 5 passes; on any failure, roll back

## Undo

`spanbackup.py` records only the byte ranges about to be overwritten, each with
a sha256 of the original bytes. Two injections into a 12 GB container cost
177 KB. The journal is fsynced before the target is touched. Restore refuses
if the target changed size since, and saved bytes that don't match their hash
never go back. A revert cut off partway can be run again.

The index is restored from the copy taken at apply time while it's still that
file and the live index is still the one the injection wrote. Otherwise the
two numbers per entry the injection changed go back in place, once every entry
is checked to be at its recorded offset and still holding the injection (or
already the original); anything else there is another mod's and the revert
stops before writing. An injection also refuses a pair whose target entries
don't already read back as their index says, so no undo is ever taken from an
inconsistent pair.

## Every language or none

`transaction.apply_injection()` treats every language archive as one unit:

```
1  validate everything, write nothing
2  write, journalling each span first
3  read back and inflate what was written
rollback on any failure in 2 or 3, every archive, in reverse
```

On a two-archive install (1.5 GB each) the undo is 679 KB against 3.0 GB.
Each run gets its own folder in the undo store, so two runs can't overwrite
each other's index copy. The game's archives are claimed first
(`fileops.claim`), so an HD install, an injection and a terminal `--revert`
never write the same white_img at once.

## Manifest type

```json
{
  "id": "install_leviathans_tears",
  "type": "archive_inject",
  "archives": ["u", "c"],
  "injections": [
    {"game_path": "vfx/field/weather/weather07/veffs.jp.win32.imgb",
     "source": "Data/vfx/field/weather/weather07/veffs.jp.win32.imgb"}
  ]
}
```

Leviathan's Tears is retired (kept as `retired: true` so an old install still
reads as in and comes off); the type remains for injections in general.
