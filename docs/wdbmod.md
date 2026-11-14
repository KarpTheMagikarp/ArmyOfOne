# FFXIIIwdbMod's .txt format

Gameplay Tweaks ships `FFXIIIwdbMod.exe`, and its `FFXIIIwdbLoad.bat` is what a whole family of XIII mods calls (Less Grinding included, usually without shipping either). `patcher/mods/wdbmod.py` does what the exe does, so neither one ever runs. This is the format as read off the exe itself (decompiled with Ghidra and checked by disassembly). The engine was then run against the real exe under Wine: every Gameplay Tweaks mod, all 17 of its versions through the drawer, and 450+ fuzzed cases came out byte for byte the same. `tests/test_wdbmod.py` pins the exe's output as golden hashes.

## What FFXIIIwdbLoad did, and what the sandbox does instead

| theirs | ours |
| --- | --- |
| `FFXIIIwdbSetup.bat` finds the game, unpacks `white_imgu` (or `white_imgc`) whole, keeps the `.wdb`s in `wdb_files\orig` | the table is read straight out of the game's archive, as the game has it now |
| `FFXIIIwdbMod.exe x.txt` edits `wdb_files\mod` (or `orig` the first time) | `wdbmod.run()` |
| `ff13tool -i` puts each `.wdb` line's file back in both voice sets | the sandbox's own `ff13tool -i` model, both `filelistu` and `filelistc` |
| `select` / `selecttext` / `selectchoice` / `filenameN` pick a .txt with CHOICE | same CHOICE, after a menu read off `selecttext`, so the drawer offers them as versions |

The built-in `FFXIIIwdbLoad` wins over a shipped one (`World.builtin` ahead of `find_program`'s search). A mod that runs `FFXIIIwdbMod.exe` itself gets `_wdbmod`, which works on `wdb_files\` beside it the way the exe does.

## A table

A WPD laid out flat, all big endian:

```
0     "WPD\0"
4     u32 entry count
8     8 bytes, kept
16    count x 32: name (16, NUL padded), u32 offset, u32 size, 8 bytes kept
...   entry 0's data, 1's, 2's, 3's, then the rows, back to back
```

The exe never checks any of this. It assumes the first four entries are the table's own (`!!string`, `!!strtypelist`, two more) and that every entry after is a row of `!!strtypelist`'s size / 4 words, each straight after the last. `wdbmod.Table` turns a table away that isn't laid out that way, because the exe would have written garbage over it.

## Lines

Lines starting `:\` and blank lines are skipped. The first other line is a table's path (`db\resident\bt_auto_ability.wdb`), then:

| line | does |
| --- | --- |
| `record,column,type,value` | exactly three commas, or the whole run fails. Record is matched case-sensitively among the rows. An empty value is skipped |
| `:NEWFILE` | write this table, the next path line starts another. A table met again picks up where it was left |
| `:NOUNINSTALL` | no uninstall file. Nothing to us |
| `:RENAME,flags,old,new` | first entry matching `old` case-blind gets `new` (16 chars, NUL padded). Flags bit 1: fine if it isn't there |
| `:COPY,source,new,where` | a copy of `source`'s row inserted as `new`, before `where`, or at `:BEGIN` / `:END`. Offsets after it shift by a row, every one by 32 for the new entry |

A line longer than 254 characters stops the exe reading, so it's an error here too.

## Types

Only the first letter counts for these three:

- `w`: `atoi(value)`, the whole word. `atoi` wraps round 32 bits rather than clamping, so `99999999999` is 1215752191, and `0.5` is 0.
- `f`: `atof(value)` to a float32 (double rounded the way `cvtsd2ss` does), its bits as the word. No hex, `inf` or `nan`: they read as 0.
- `l`: a string. The word becomes its offset in `!!string`. Only a string's start is a match, and a value starting with `0` points at the first NUL. A new string goes after the last string's own NUL, the pool re-padded to 4, every later entry's offset moved by the growth and entry 0's size set to the padded size.

Anything else is a bit field in the word, walked from bit 31 down:

- `b` / `c` skip 8 bits, `s` / `i` skip 16, and either turns `t` and `p` back off
- `t` then each `0` skips 1 bit
- `p` then each hex digit skips that many (`0` is 16)
- the last letter is the width, read the same way: `b`/`c` 8, `s`/`i` 16, `1` after `t` is 1, a hex digit after `p`

So `bs` is bits 23..8, `ib` 15..8, `ii` 15..0, `bbbb` 7..0, `p347` 24..18 and `p47E7` 6..0. The value is `atoi`'d and its low bits go in; nothing else in the word moves.

## Where ours differs, on purpose

- `:COPY ... :END`: the exe reads the new entry's offset from one slot past the end of its table, so it writes a broken one. Ours puts it where it was meant to go.
- A column past the end of a row, or a `:COPY` name over 16 characters, writes out of bounds in the exe. Ours stops with the reason.
