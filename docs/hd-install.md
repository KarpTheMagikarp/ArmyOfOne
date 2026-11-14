# Installing the HD mods

The FF XIII HD Project and FF XIII-2 HD each ship three tools and a batch file
that sequences them. Every failure people report lives in the batch files, so
the app keeps the tools and replaces the sequencing. This is what that
sequencing promises, and how. XIII-2's differences are at the end.

The one rule: a file in the game is either what it was or all of what it is
going to be. Never something in between, and never reported as the wrong one.

## Order of a run

```
fixes       the game's required fixes Apply hasn't put on yet, through Apply's own run
preflight   variant, space on every drive involved, path lengths, locks
backup      every archive the run will touch, before anything else
characters  unpack white_scr, run the tools per id, repack, read back, swap in
zones       per zone: tools per block, import into its own pair, commit
```

Characters go all the way into the game before the zones start, so a zone that
fails later leaves the finished characters where they are.

The fixes go first so dropping the mod in before pressing Apply isn't a dead
end, and so the backup is taken with them in, the same order Apply then
install always gave. Apply's scan of the disk decides what's missing, so
nothing already on goes in twice, and only what ships with the app goes in
unattended. A fix that won't go in is said in red and left to the install's
own checks; Stop during them stops before the mod.

Where it goes faster without checking less:
- XIII-2's per id steps run a few at once (`hd_runner.SIDE_BY_SIDE`, cores less
  one, four at most). Its injector and model tool were traced under wine
  opening nothing but their own id's `models\orig` and `models\mod`, the mod's
  art and data read only, and the model tool's output matched byte for byte
  run alone and side by side. A model's mesh edits still start after its
  textures are in, every step is settled and said in turn as it finishes, and
  the verdict waits for the whole batch. XIII's builds of the tools haven't
  been traced, so they still go one at a time.
- The seed caches four files at once, each still written whole and flushed.
- Reading every entry of a backed up pair goes in a few lanes, one handle
  each, and still names the earliest that won't read.
- The rebuilt pair is renamed into the game, flushed first, when the mod folder
  is on the game's drive, instead of copied. A Stop is asked before each.

## What each write does

| write | how |
|---|---|
| backup | each pair staged together, fsynced, renamed in. Size and sha256 go in `backup.json`, and each half of a pair records the other's sha256 |
| character pair | read back through the index first, both halves staged, swapped in together |
| zone | working pair read back, blob staged and swapped, index imported under a guard, all undone together on failure |
| restore | every file the backup holds hashed against `backup.json` first, every pair checked to have been taken together, then scr pairs, image pairs, each blob, hashed again while staging |

"Staged" is `fileops.stage_copy`: a temp sibling on the same volume, hashed on
the way through, so verifying a 4GB file costs no second read. "Swapped" is
`fileops.replace_together`: each target steps aside to a `.prev`, the new one is
renamed in, and any rename that fails sends the others back. On Windows a
rename refused by a sharing violation is retried for a few seconds first,
because antivirus opens every freshly written file to scan it.

If a rename fails and so does the way back, that is `Stranded`, never a quiet
mismatched pair. The message names what is out of place, the game's own copy
is still beside it as `.prev`, and Revert puts it right.

`backup.json` also says when the game was first written from the backup. The
mark goes down right before the first byte moves, and comes back up when that
swap went back whole or a restore finished, so a backup that was cut short, or
one the game has already been put back to, is never copied over whatever the
game has become since.

An index and its archive are only ever a pair if they were taken together. A
live XIII-2 had filelistu renamed out of its backup so it could be sent
somewhere. The next install took the game's filelistu, which an earlier run had
repacked, and kept white_imgu from before that run. Revert put that pair back
and the game crashed right after the logo: none of the index's 14381 entries
sat where it said they did, and 783 pointed past the end of the archive.

So `back_up()` stages everything it's missing first, then asks the whole
backup as it would stand, every voice set and zone included, whether it still
goes back as one game (`hd_install._apart`), and only then renames anything in:
- a file the backup was taken with that's gone from the folder only comes back
  as that exact file, by the hash of the bytes actually staged against the
  one on record, so a file rewritten mid-copy is caught too. Anything else and
  the install refuses and says to put it back
- each half of a pair records the other's sha256 once the pair is checked, and
  a partner on record is never talked round by bytes that happen to read back
- a pair with no partner on record (an older build's, or one a cut left
  unrecorded) has every compressed entry read back
  (`hd_zones.pair_problem(samples=None)`). A sample is enough after a repack,
  where everything moves, and misses an injection like Leviathan's Tears,
  which rewrites two entries in place
- each zone blob is checked against the index white_img keeps for it
  (`hd_zones.zone_problem`): an index from after a zone install points past
  the end of the vanilla blob
- a game whose own pair is already scrambled is refused rather than backed up

An older build's files that pass are recorded and partnered, all of them and
not just the ones this run needed, so they're read through once. The main
listing is read once however many zone blobs share it, since a real one takes
half a second.

`restore()` asks the same of the whole backup before anything moves, refuses
anything on record that's gone from the folder, and checks the whole backup
even when a rebuild only puts one voice set back: the rebuild backs up again
straight after, and a refusal then would come with the install already gone.
Every refusal says what to do: put back a file that was moved out, or verify
through Steam, delete the backup and run Revert again. Verifying alone leaves
the backup marked as written, so every later Revert would refuse.

Not handled: a power cut inside `retake()`'s swap can leave a half as `.prev`
in the backup folder, and the refusal that follows says to put the file back
without naming the `.prev`. The verify, delete and Revert route still works.

One writer at a time per game: the window's install, an archive injection going
in or coming off and a `--revert` typed into a terminal
all take the same lock in the undo store, which the OS drops with the process
however it ends. The window also refuses to start Apply on a game during its
HD run and the other way round, so it says so up front rather than at the lock.

## The zone commit

A zone is a blob loose on disk plus an index that lives inside `white_img<v>`.
ff13tool edits that archive in place, so the commit is:

1. read the working pair back: every original path still listed, nothing past
   the end of the blob, the newest entries inflate
2. stage the new blob beside the game's (Stop still works here)
3. swap it in, then take an `ImageGuard`: the image's length, the span the old
   zone index sat in, and a copy of the index
4. run the import, uninterruptible
5. read the imported entry back out of the game byte for byte

Any failure after step 3 truncates the image, puts the span and the index back,
and swaps the old blob back. An import that never got the image open changed
nothing, so nothing is written to undo it: the lock that stopped the import
would only stop the undo too. If ff13tool moved other files around inside the
image, the guard cannot undo that and says so, pointing at Revert rather than
calling it rolled back. Anything that could not be proven undone reads as "may
be half in the game" in the ending, never as "nothing changed".

"Moved" is judged record by record, never by path. XIII-2's filelistu lists
274 paths more than once, each copy at its own sectors, and a lookup by path
keeps one copy and reads every other as moved. On XIII-2 the listing compared
is the decrypted copy ff13tool was handed, since that is the one it rewrote.

A block is a `.trb` of texture headers and an `.imgb` of the pixels they
address. The `.imgb` import waits on the `.trb`, and a zone with any failed
import is left out and reported as a gap. Half a block is the coloured static.
A gap also keeps whatever build stamp the zones had, so an older broken zone
left in place still reads as one.

## The originals cache

The tools read vanilla models out of `models\orig` and write HD ones into
`models\mod`, so a repair is only as good as that cache. Dropping a changed
archive clears the unpacked folder and unpacks it again, carrying the cache
across, since it comes out of the game rather than the mod. When it is missing
anyway, any model the game carries bigger than the backup does is read out of
the backup instead of the game's tree. Bigger, because HD art is bigger art,
and a backup taken of a game that was already modded is the other way round.

Nothing bigger than its vanilla size (below) is ever cached as an original,
whichever way it got there: their installer refills the cache from the live
game, and a backup taken over a hand install is as modded as the game. A
cached copy that is bigger is cached again from the backup or the game, if
either is clean; the voice set re-cache never swaps a clean copy for a backup's
modded one. A zone blob is copied again whenever the cached one isn't the
source's size, and its index is read out of the source every time, the way
XIII-2's harvest already worked.

Before anything is written, every id the plan would build is checked the same
way (`hd_install.unbuildable`). One the game carries modded with nothing clean
to build it from, cached or backed up, is left out and said in the terminal,
since what's there is already modded and building over it is HD art on HD art.
A zone whose source blob isn't its Steam size is left out the same way. If that
is everything ticked, the run refuses before the backup with Steam's Verify as
the way forward.

Their installer fills the same cache from the live game, so a populated
`models\orig` with no backup of ours beside it is warned about before the first
backup. Our own runs leave `models\orig\patcher.txt` once the backup is taken,
so the cache our seed filled doesn't read as theirs after a Verify and a
deleted backup. The archives are measured on every first backup either way.

A run that works clears what no later run reads (`hd_install.clear_caches`):
`models\mod`, which is in the game already, and the zone copies, which the next
zone run reads out of the backup again. For XIII that's 4.49GB of blobs a voice
set plus their extractions, which their own cleanup means to delete and misses.
XIII keeps `models\mod\chr\pc` and `chr\npc`, the two its cleanup keeps
because other mods read them. XIII-2 also drops its fixed copy of the model
tool, made again by the next run. `models\orig\chr` stays, since the mod and
HD Models Plus both build from it. A run that fails keeps everything, because
that's the evidence.

## What's already in the game

The backup answers it exactly, for a game this app saw clean. Everything else
(no backup, a copy from another PC, their `.bat` run first, an undo folder
deleted, a backup an older build took over a hand install) goes by
`manifests/vanilla/<game>.txt`: every character model's size in a vanilla Steam
copy, read off the real indexes in `tests/data` by `tools/vanilla_models.py`.
Both are clean where it counts: XIII-2's has never been through `ff13tool -c`
(it stores `sys/debug/version.txt` raw where Square Enix compressed it), and
XIII's matches the sizes their `Setup.bat` checks and XIII-2's clean copies of
every model the two games share. Zone blobs go by the size tables both setups
check.

A model counts as changed when either half is bigger than vanilla, or isn't
the backup's copy where that copy isn't bigger itself. Bigger only, so a table
wrong about a file can only miss it, and the Japanese set can't read as modded
for differing from the English one the table came off. The backup still
catches what the table can't size, and a backup taken over a hand install
can't make a verified game read as modded. XIII-2 categories no run of ours
wrote count by their groups, at a quarter of the models grown: the injector
only touches models it ships art for, half the weapons on the real run.

So the drawer badges a game with no backup from the game itself, the record
never stands in for it while the index reads, a first install over a game with
some of it in already says so (in the terminal, before the backup that keeps
it), and Revert and the restore say when what they put back still carries
models that were modded before this app's first install.

## Revert

`--revert` on XIII puts the HD Project back from its backup, placed among the
other patches by when the backup was taken: an archive injection made after it
comes off first, one made before it is inside it. It goes by what the backup
holds rather than by today's voice setting, so a game modded once with English
voices and once with Japanese gets both back.

An injection with anything in the files the restore puts back waits for it,
then goes pair by pair on what actually came back, never on clocks: the backup
is taken once and kept, so a copy older than this application can still hold
an earlier one. A journal that fits the restored file and finds its original
bytes there means the copy came back without the injection, so that part is
forgotten, never replayed into a file it no longer describes, unless the pin
finds the payload there too: then those originals are its own bytes, journalled
by a second apply from a build before Apply learned to leave a pair that
already holds it alone, and it is said and left recorded. Anything else
needs positive proof before a replay: the manifest's pinned hash finding the
payload in the restored pair, with its entries at the sectors the journal was
taken at. Another mod's bytes in the same place are not ours to put back, and
a copy laid out differently of the same size would send the replay somewhere
else. Proven there, the journal is replayed and the game's clean pair is taken
into the backup again, so the backup stays one pair with the zone blobs its
indexes point into and a later HD install and Revert cannot bring the
injection back. The retake is staged, and unrecorded under a `retaking` mark
while the swap happens; a swap that goes back whole gets its old record back. Proven absent, it is
forgotten. Neither, it is said and left recorded. Anything else it wrote
(Leviathan's Tears in a voice set no HD install has touched) comes off by its
own undo. If the restore does not finish, the injection is left in and
recorded, and the next Revert takes both.

It only takes back what this app wrote. If an archive has grown past its
backup anyway, which is what the mod's own installer run by hand looks like,
it leaves it and says so rather than calling the game clean. Smaller or
missing is not flagged: that is a voice pack taken off, or a backup that was
itself of a modded game, and no Verify would ever clear it. A rebuild is the
other way round: it needs its voice set vanilla whoever changed it, and leaves
the other voice set alone unless the voices box is ticked, which puts both back
first, since the copy after it only adds.

A rebuild asks everything it can refuse on without the archives (the
categories, the 4GB flag, a zone blob neither the game nor the backup holds)
before its restore, so a refusal is not the second half of a click that
already took the install off. If the copy that went back does not hold an
injection, its own undo takes the rest of it off too, the terminal says so,
and it is no longer recorded. A rebuild handed the game's required fixes puts
it straight back on before the mod; otherwise Apply offers it whole. A copy
that holds it keeps it, recorded, under the rebuild.

Apply itself leaves any pair that already holds every payload alone, and the
record keeps the earlier run's undo for it. Written again, the pair's journal
would hold the payload as its own original, and nothing could take it off. An
earlier journal that has gone missing is still carried, by its file name, so
Revert refuses it as incomplete rather than put back the index without the
data under it.

## FF XIII-2 HD

Same guarantees, same backup, swap and Revert code. What differs:

| | XIII | XIII-2 |
|---|---|---|
| character archive | `white_scr<v>`, its own pair | `white_img<v>`, the same archive the zone indexes live in |
| indexes | in the clear | encrypted, opened for ff13tool and sealed again after |
| categories | the mod's `LoadHD*` lists | five texture folders and two model lists, hardcoded in their `.bat` |
| zones | listed in `LoadHDEnvironments.txt` | the `z####` folders under `textures` |
| originals cache | `models\orig` | `models\orig` for characters, their `zone\` folder for zones |
| already modded? | every model's vanilla size, the six their `Setup.bat` checks among them, and the twenty zone blobs in both voice sets | every model's vanilla size, the three their `HD2Setup.bat` checks among them, the fifteen zone blobs in both voice sets and the English zone indexes, read off a clean Steam copy |
| before it starts | | the 4GB Patch goes on with the other required fixes, and it refuses if the flag still isn't on `ffxiii2img.exe`. Their `.bat` sets it itself |

Their `.bat` has no uninstall and no choice of what to install, so the backup
and the toggles are new rather than a repair of something that was there.

Sealing writes the index checksum, which theirs never does. ff13tool v1.4
leaves a zero there after `-c` and `-i`, and only fixes it by running
`ffxiiicrypt` out of its working directory, where their `.bat` never puts it.
Every vanilla index carries the right value. Our decrypt and encrypt were run
side by side with `ffxiiicrypt` under wine and match it byte for byte;
`tests/data/hd2/real` holds those outputs and the tests hold us to them.

`tests/test_hd2_real_tool.py` runs a whole install through the real
`ff13tool.exe` when `FF13TOOL` points at one. On Windows it needs nothing else.

`FF XIII HD Models.exe` v4.0.0 can spin for hours on a model with nothing wrong
with it. Every recipe the mod ships declares zero name swaps, so the swap table
is allocated empty and never written, and the swap pass bounds its loop on
`size - strlen(table[0])`, unsigned. Heap junk in table[0] longer than the chunk
being scanned wraps that to about 4.29 billion turns. n111 has a 4-byte chunk.
On the first real run it froze 1288 steps in; reproduced under wine with the
heap filled, it was caught at position 869 million of the wrap. Which models
trip it depends on what the heap held, so it varies by PC, which fits the
"stuck on Updating Models for hours" reports on their Nexus page.

So the models run through `FF XIII HD Models.fixed.exe`, a copy written beside
theirs with one byte changed: the jump the pass takes on an empty table goes to
the return instead of the position step. It's only made when their exe is the
exact v4.0.0 build, checked by hash before and after, and their exe is never
written. Under the same filled heap the copy finishes in seconds where theirs
took a minute, and the `.trb` it writes is byte-identical. Any other build runs
as it is.

Either way each recipe gets five minutes, against seconds normally. Past that
the tool is ended and the id is left out and counted as skipped. Its textures
from the injector pass are already in the tree, so all it loses is the mesh
edits.

## Still unexercised on real hardware

- the Japanese `c` archives, which the Steam copy ships beside the English ones,
  picked when the launcher's `voice_mode` is 0
- whether ff13tool relays out the game's own image on an import. v1.4 appends
  under wine on a small archive, keeping record order and rewriting every copy
  of a repeated path, which the guard undoes exactly, and it is honest about
  anything else
- console output from the windowed exe, which borrows the terminal it was
  started from and hands it back before a revert moves anything
- the Windows half of the one-run-at-a-time lock, an msvcrt byte range lock
- the fixed model tool on Windows. The bug and the fix were reproduced under
  wine on the real n111; the first real run had already put the injector
  through weapons and all fifteen zones, and their model tool through every
  recipe up to n111
- any line starting "Error" failing an ff13tool step. v1.4 only prints one
  when something failed, measured on XIII-2's copy; XIII's HD Project ships
  its own copy of the tool
- an index ff13tool left unreadable mid-import is taken as nothing moved, and
  put back. v1.4 reports the image written before the index, and only appends
