# The environmental zone pipeline

How the HD Project's Environments category installs, read off `FFXIII HD
Installer.bat` and `FFXIII HD Setup.bat` and checked against real game files.
Implemented in `patcher/mods/hd_zones.py`.

## The nesting

Everything else the mod installs lives in one archive pair: unpack `white_scr<v>`,
write models into the tree, repack once. A zone does not. The game keeps each zone in
its own pair and the two halves are not in the same place:

```
white_data\sys\white_img<v>.win32.bin        holds zone\filelist_z010u.win32.bin
white_data\zone\white_z010u_img.win32.bin    sits loose on disk
```

Checked against a real `filelistu.win32.bin`: 14,107 entries, 99 chunks, 2.13 GB
uncompressed, 23 `zone/filelist_z###u.win32.bin` entries inside it totalling 659 KB.
The mod touches 20 of the 23, skipping z000, z105 and z106.

## Blocks are pairs

A block is two files:

```
bg\loc014\m022\bin\block022.win32.trb     texture headers, mesh
bg\loc014\m022\bin\block022.win32.imgb    the pixels those headers address
```

Their `:LOADMODELFILE` runs `ff13tool -i` twice, once per half:

```bat
ff13tool -i %1 %2 %3.trb
ff13tool -i %1 %2 %3.imgb
```

Importing only the `.trb` succeeds on every step and renders every environment
surface as coloured static, since HD headers then address standard pixels. The
chr pipeline carries both suffixes in `Step.copies`, so characters aren't
affected.

Whole-zone corruption (rather than a few surfaces) means the index and the blob
disagree. `hd_state.half_written_zones` detects a .trb changed without its
.imgb and the panel flags it before launch.

## The sequence

Setup, once per zone:

```
ff13tool -x -ff13 filelistu.win32.bin white_imgu.win32.bin
robocopy white_imgu\zone models\orig\zoneu filelist*u.win32.bin /S /XN /XO /XC
robocopy <game>\white_data\zone models\orig\zoneu *u_img.win32.bin /S /XN /XO /XC
ff13tool -x -ff13 models\orig\zoneu\filelist_z010u.win32.bin models\orig\zoneu\white_z010u_img.win32.bin
robocopy models\orig\zoneu\white_z010u_img\bg models\orig\zoneu\z010\bg * /S /XN /XO /XC
```

Then per zone, their `GENERATEZONE`:

```
copy models\orig\zoneu\filelist_z010* models\mod\zone\
copy models\orig\zoneu\white_z010*    models\mod\zone\
for each block dir under models\orig\zoneu\z010\bg\loc014\:
    del /Q models\mod\<basedir>\bin\*.*
    if Data\z010block<n>u.txt exists:  FFXIII_ModelsHD.exe Data\z010block<n>u.txt
    else:                              FFXIII_HDProject block<n>.win32 ... zoneu\z010\ /s
    if the tool reported doing something:
        cd models\mod\zoneu\z010
        ff13tool -i ..\..\zone\filelist_z010u.win32.bin ..\..\zone\white_z010u_img.win32.bin <block>.win32.trb
        ff13tool -i ..\..\zone\filelist_z010u.win32.bin ..\..\zone\white_z010u_img.win32.bin <block>.win32.imgb
        set updated=1
if updated == 0: stop, write nothing to the game
cd models\mod
copy /Y zone\white_z010u_img.win32.bin <game>\white_data\zone\
ff13tool -i <game>\...\filelistu.win32.bin <game>\...\white_imgu.win32.bin zone\filelist_z010u.win32.bin
del the working pair and the extracted tree
```

That last import is the only write to the game before a run ends, since the
zone's index goes back into an archive in the game with the blob beside it.
Every other pipeline builds in the mod folder and copies at the end.

`/s` is on their zone injector call and neither chr call. It's undocumented and
kept because their zones ship working with it.

## Reading ff13tool's output

Its exit code is 1 either way and is not a status. `-i` is judged by the four words
their own `for /f "usebackq"` counts, matched on the **first token** of each line,
because the tool prints the word with its argument:

```
Added loc006/m001/bin/block001.win32.trb (compressed)
Updated ..\..\zone\white_z002u_img.win32.bin
Imported one file to ..\..\zone\white_z002u_img.win32.bin
Wrote ..\..\zone\filelist_z002u.win32.bin
```

The printed path drops the leading `bg/` and the verb is `Added`, but no
duplicate is created: `File list processed. 1586 files listed in 12 file
chunks.` stays at 1586 across all twenty of z002's imports.

## Differences from their scripts

**No 2 GB extraction for the indexes.** Theirs unpacks the whole of `white_img<v>` and
robocopies 659 KB out of it. An index gives the sector offset and both lengths of every
entry, so `hd_zones.read_entry` seeks to the one it wants.

**The bg folder is walked, not named.** Their installer hardcodes one per zone
and gives z029 `bg\loc019`, which is z019's. Walking can't name the wrong
folder or miss a second area.

**Cleanup.** Their `CLEANUP` deletes `models\orig\zone<v>\white_z1*.bin` and
`white_z2*.bin`, but every zone is `z0`-something, so 4.49 GB stays in the mod
folder. Their per-zone `del` at the end of GENERATEZONE works and is kept.
`hd_install.clear_caches` does what `CLEANUP` intended once a run succeeds;
later zone runs read their pairs from the backup, not this cache.

## Backup coverage

`Prep.extra` lists game files a run overwrites besides the scr pair: for zones,
`white_img<v>`, `filelist<v>` and one blob per zone. They're backed up and
restored with the rest and never copied into the mod folder, since the zone
steps write them in place.

Revert has no run or category list, so it can't use `prep.extra`;
`hd_install.backed_up_extras` reads the backup folder and matches by name
shape.

## Still unexercised on real hardware

- The Japanese `c` variant, anywhere in the app.
- `/s` on the injector. Matched from their script, not observed.
- Whether z029's archive really contains `bg\loc019`. The walk makes it moot.
