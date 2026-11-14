# Any other mod

Everything that isn't one of the two HD Projects goes through one door:
`UiBridge.install_mod`, which hands most of it to `patcher/mods/game_files.py`.
Drop a folder, a `.zip`, a `.7z`, a `.rar`, a `.tar` or a Nova Chrysalia
`.ncmp` (a zip by another name) on the Mods drawer and it works out what's in it
from the files themselves, a `.dll` by its own headers. Nothing has to be on a
list.

The one rule is the same as the HD install's: a file in the game is either what
it was or all of what it's going to be, and taking a mod off puts back exactly
what was under it.

## The drawer

- **Worth getting** lists recommended mods the patcher can't fetch itself, each
  with Open page ([downloads.md](downloads.md)). **Installed** lists what's in.
- Mods are named by what they are, not their download name
  (`patcher/mods/titles.py`): `(STANDALONE VERSION) HD Fonts and GUI - Base mod`
  is HD Fonts and GUI, and Nexus's `Less Grinding-73-1-3-2026-08-27T12-00-00Z`
  is Less Grinding with v1.3 beside it. Two rows that would otherwise look
  identical keep their full names. Updates still match by the record name.
- The HD Project shows under Installed once any of it is in, with how many
  categories; its Remove is the Revert to the pre-install backup and asks
  twice.
- A row only gets a warning line when something's wrong: its dll gone, its
  files no longer in the game, DLC files a Remove couldn't take, a ReShade that
  won't load, an FF13Fix-based dll that disables NovaFix's frame pacing.
- Both drawers open on their last contents (per game) and re-read underneath,
  redrawing only what changed.
- The line beside Close shows one step at a time (Reading, Unpacking,
  Building, Installing, Checking your game, Downloading) with a percent
  weighted by what each part of the step costs, 100% only when the step is
  done. A `.rar` reports how much is unpacked instead, since it can't be
  listed without unpacking. The result is one sentence; details are behind
  the (i).
- A drop anywhere on the window counts, and the zone is also clickable. The
  drop listener is on `window`, not `document`: pywebview serialises the node a
  listener sits on with every event, and on `document` that was the whole page
  (about 360ms and 6.6MB per dragover).
- Updating a `.dll`/`.asi` add-on replaces its code but only replaces its
  `.ini` if it's byte identical to what we installed; otherwise the new one
  lands beside it as `.ini.new`.

## What it recognises

| in the drop | what happens |
|---|---|
| the HD Project or FF XIII-2 HD, as a folder or any archive | its options open, same as before |
| files laid out like the game's archives (`chr/pc/c201/bin/c201.win32.imgb`) | put into every archive that lists them, both voice sets |
| a `.dll` or `.asi` | NovaFix's add-on folder, through the sieve that was already there |
| a `.dll` named after one of Windows' (`version.dll`, `winmm.dll`, `dinput8.dll`...) | beside the game's exe with the folders it sits among, see below |
| one of those built for 64-bit | left out with its x86 build going in, or said on its own: a 64-bit one beside a 32-bit exe stops the game starting |
| a `.NET` `.dll` or `.exe` (a CLR header) | the mod's own installer: said, never an add-on |
| a zip inside the drop, the way a patcher carries its payload | opened and read as part of it, without unpacking anything |
| a `.7z` or `.rar` inside the drop | unpacked beside the drop by Windows' own tar, the way a dropped one is, and read as part of it; one tar can't open is said |
| a FOMOD installer (`fomod/ModuleConfig.xml`) | its options asked one group at a time in the drawer's version question, and what the answers come to read like any drop, see [FOMOD installers](#fomod-installers) |
| files the game reads loose off the disk, a movie in its `movie` folder, LR's `dlc` folder, an LR zone's archive swapped whole | in over the game's own with those kept to put back, or a new file in a folder the game has, see [Loose files](#loose-files); a zone's archive that doesn't fit the index the game keeps for it, comes with its own, or is XIII's or XIII-2's, where the HD Project builds into them, is said and left |
| records inside one of the game's containers, laid out the way WPDtool unpacks them (`db/resident/_wdbpack.bin/r_dlc.wdb`), a Nova pack's or any drop's | the container built around them over what the game has now and put in with the drop's other files as one mod, see [Records inside a container](#records-inside-a-container) |
| an index and its archive shipped whole (`filelistu.win32.bin` and `white_imgu.win32.bin`), the drag them into `sys` kind | only the records that differ from Steam's taken out and put in, see [Whole archives](#whole-archives); half a pair, or one listing files the game doesn't have, said |
| LayeredFS for LR, `dinput8.dll` and `layeredLS.ini` with a `mod_data` folder | `mod_data`'s files into the archives like any game files, and the loader left out, said as not needed: on LR, `dinput8.dll` is where Savior's Patch goes |
| a ReShade preset on its own, or a ReShade add-on (`.addon32`, ShaderToggler's say) | beside the ReShade that's in, through the loader door; an add-on only loads in ReShade's build with full add-on support, so with the patcher's own standard build in it's said rather than put in, and a 64-bit one is said |
| an xdelta patch on its own (`.xdelta`, `.vcdiff`) | matched to the file it's for by the name in its header or its own: a loose file's decoded against the game's copy and goes in over it, an index and archive pair's go in record by record like the Console Content Patch's |
| the game's own exe, a save (`ff13-2-00.dat`) | said: the exe stays Steam's, and a save goes in the folder the player's own are in, `Steam\userdata\<id>\<app>\remote` with Steam Cloud on |
| a Cheat Engine table (`.CT`), a TexMod package (`.tpf`), a spreadsheet | said for what it is: nothing of it goes into the game |
| a 4GB patcher (`4gb_patch.exe` and the like) | said: Apply's 4GB patch does the same |
| a Nova Chrysalia modpack | named by its `modconfig.ini`, read UTF-16 the way Nova writes it or UTF-8, `EN-Data` for the English voice set and `JP-Data` for the Japanese, sent to its game by `GameEntry`; its `External` installer played the way Nova runs it and its `Code` patches applied through NovaFix, see below |
| a file Windows Security won't hand over, the drop, a member or a zip inside it | said as a threat block, with what to do if the mod is trusted |
| the HD Project's zip in a dropped folder | opened the way it would be dropped on its own |
| the HD Project zipped inside another zip | said: take it out and drop it by itself |
| HD Models Plus's own download | its three versions offered, then the one picked built with its own model tool and put in like any mod, see [HD Models Plus](#hd-models-plus). Its Nova Chrysalia versions go in as they are. Either way Playable Characters on the HD Project reads as in once all six of the party are |
| a `.bat` around its author's tools, xdelta and Cocoon, like XIII-2's Console Content Patch | read rather than run, and what it would have done put in as one mod, see [A mod's .bat](#a-mods-bat) |
| any other installer `.bat`, ff13tool and MJB's tools, a model builder of the author's own, menus and language questions | played on a copy of the game, its menu offered as versions, and what it left the game's files as put in like any mod, see [Any other installer .bat](#any-other-installer-bat); one that puts nothing in is played again on a PC changed the way it wanted, or said by what stopped it, see [A .bat that puts nothing in](#a-bat-that-puts-nothing-in) |
| one of Surihix's LR patchers, a .NET exe beside a `PatchData.bin` | read rather than run, in both voice sets, with its DLC files beside the game, see [LR's patchers](#lrs-patchers) |
| ReShade (a dll exporting `ReShadeVersion`, whatever it's called) | into a `ReShade` folder beside NovaFix as `d3d9.dll`, with its settings, presets and shader folders, through the loader door; a 64-bit build left out and said |
| .NET libraries an older drawer put in NovaFix's add-ons | off the list, and taken out while they're as they went in |
| files under another game's data folder (`alba_data` dropped on XIII) | sent to that game |
| files only another installed game has, a `.dll` among them or not | sent to that game |
| two different files for one place, one per folder | the folders offered as versions |
| an `Optional` folder inside the mod with its own copy of some files | offered as a version laid over the rest |
| a model's `.trb` going in without the `.imgb` the mod has somewhere else (another version, the other voice set) | refused, nothing written |
| empty files, a zip listing one name twice with different bytes | left out and said |
| a zip that won't open | said to be an unfinished or damaged download |
| zone art (`bg/...`), `.dds`, an installer `.exe` on its own (with a pointer to its Nova version, the "(Packed)" downloads have one), an xdelta for half a pair or the exe | said plainly, nothing written |

Matching goes by the longest tail of a file's path that's a whole entry in one
of the game's indexes, so whatever sits in front of it (`Data\`, `alba_data\`,
`My Mod v2\`) doesn't matter, and two files with the same name in different
folders never swap. The folders the drop itself sits in count too: dropping
`white_imgc` itself, or one file out of an unpacked archive, says the same as
dropping the folder around it. A folder named like an unpacked archive
(`white_imgu`, `_white_scrc.win32.bin`) says which voice set its files are for,
and a file meant for one set wins over one meant for both; the nearest data
folder says which game. A file none of whose own folders are the game's
(`Serah Dress\c108.win32.imgb`, the usual zip) goes by its name, only when the
game has that name exactly once, and a mod named after one of those folders is
named after the folder around it instead.

The same file twice in one mod is one file. Two different files for one place
is a mod that comes in versions, and when every clash splits along one folder
(`Short Hair/` against `Long Hair/`) those folders are offered as a choice,
under Dropped at the top of the drawer, with one Install; nothing's picked to
start with, since the wrong hair is still the wrong hair. The other versions'
files stay out entirely, not only the clashing ones. A folder
of options inside the mod's own files (`Outfit/` and `Outfit/No Cape/`) is a
version too, laid over those: picking it takes its files and the rest of the
mod's. Whatever's picked, a model's `.trb` and `.imgb` have to come from the
same place. One going in while the drop has the other somewhere it isn't going
in from is half a model, and nothing is written.

An empty file is left out: an entry of 0 bytes is how an index marks a
placeholder, so it could never be told from one or come back off, and it's
usually an unpack that didn't finish anyway.

## Mods that load themselves

Some mods aren't files for the archives or NovaFix add-ons at all: a `.dll`
named after one Windows ships, put beside the game's exe so Windows' loader
finds it before the real one, with the folders it reads its data out of. HD
Fonts and GUI is `version.dll`, `hd_textures` and `hd_textures_shaders`, and its
readme says all of it into the folder `ffxiii2img.exe` is in.
`patcher/mods/loader_mods.py` does that.

What goes with the `.dll` is everything it sits among in the drop: every folder
beside it, its config and any other code beside it. Not its readme, not a
`screenshots` folder, and never a game file, an installer or a folder holding
either, which are the mod's other parts and go their own way. A game file the
archives don't list is still a game file (another game's, usually), so it's
left out and said rather than put beside the exe. The `.dds` in `hd_textures`
are its data, not art that needs packing.

NovaFix turns its own fixes off, frame pacing and all, when a `.dll` beside it
is FF13Fix or built on it (its `compat/modules/foreign_fix.cpp`: the
`FF13Fix.ini` name with one more of its markers beside it), and won't load
one from its add-ons at all. `loader_mods.ff13fix_built` reads a `.dll` by
that same rule, once a version, and the row says so in the warning colour,
as does the install; Apply of NovaFix names one put beside it by hand. The
HD rows (the HD Project, HD Models Plus, HD Fonts and GUI) say when the exe on
disk hasn't got the 4GB flag, which those need the memory of.

Only a 32-bit `.dll` goes there. These games are 32-bit, and Windows loading a
64-bit `version.dll` in place of its own stops the exe starting, so a drop with
an `x86` and an `x64` folder puts the x86 one in and says it left the other,
and a 64-bit one on its own is turned down. What sits with the 64-bit one, its
plugins and its data, is that build's by the same rule and stays out with it,
rather than its `.asi` going to NovaFix's add-ons or its `.dds` reading as art
to pack. The same goes for a proxy Windows Security blocked, whichever build it
is: its files stay out with it and the block is what's said. Two different files that would land
on one path beside the exe, two options each with its own `hook.ini`, is a
refusal with nothing written, since there's no telling which is meant.

Nothing beside the exe is written over unless this app put it there for the
same mod. A file this app installed itself is named (NovaFix is `d3d9.dll`, the
4GB compatibility shim is XIII's `dinput8.dll`), another loader mod's is named
too, since two mods can't both be `version.dll`, and anything else is somebody
else's. Each file is written beside its target and read back first, then
swapped in with whatever it replaces moved aside until the set is in, so
antivirus quarantining a flagged `.dll` as it lands shows up as the read back
failing, before anything was swapped. The record goes down before any of it
(`.patcher-undo/loaders/`), with the hash of every file and what each may hold
while an update is part way, so a crash leaves something Remove finishes.
Remove takes off exactly what went in and the folders it made, and leaves a
file that's changed since. An update replaces its own files, drops what the
new version doesn't ship, and keeps a config the user has edited, with the new
one beside it as `.new`. A `.new` the user has since edited too, their settings
merged into it, stops the update with nothing changed rather than being
written over. An update is the same name with a file in common: a
second `Release.zip` that shares nothing with the first is another mod, and
goes in beside it as `Release (2)`.

An update the power cut short is finished by dropping it again: the record
still names the version before it, so what that one had and this one doesn't
comes off too, along with the `.patcher-new` and `.patcher-old` copies the cut
left beside the files. An old copy antivirus is holding open when it should be
deleted stays on the record, and the next install or Remove takes it.

ReShade goes through the same door, under its own name. NovaFix only loads it as
`ReShade\d3d9.dll` beside itself, so a dll exporting `ReShadeVersion` (or an
`.asi` named for ReShade, the build renamed for an ASI loader) is never a
loader over NovaFix's `d3d9.dll` and never an add-on: `game_files._claim_reshade`
maps the build to `ReShade/d3d9.dll`, and with it:

- the settings that build reads, as `ReShade/d3d9.ini` (4.x reads its own name
  first) or `ReShade/ReShade.ini` (5 and on, which a stale `d3d9.ini` beside it
  doesn't beat). Settings are a `[GENERAL]` that names a search path or a menu
  key; a `[General]` alone is half the `.ini` files there are. The ones it
  doesn't read stay out, and out of NovaFix's add-ons too
- every preset beside it (an `.ini` or `.txt` with a `Techniques=` line), its
  shaders and add-ons (`.fx`, `.fxh`, `.addon`), and a flat package's textures
- the folders beside it that are ReShade's: named like its own
  (`reshade-shaders`, `Shaders`, `Textures`, `Presets`), holding shaders or a
  preset, named by a search path in its settings, or holding nothing but
  pictures and notes (no `.dds`, which is game art) when no other mod's code
  shares the build's folder
- what a search path that reaches outside the build's folder, or a whole path
  from its author's PC, points at in the drop: only what that setting looks for
  (shaders, pictures, presets), the folder's own files unless the path ends in
  `\**`, never another build, a readme folder or a folder holding another mod.
  It goes under `ReShade/` by the path's last folders and the line is rewritten
  to match; nothing else in the file changes. A whole path naming a preset
  that's beside the build means that one

Another mod's folder stays its own, and so does a folder of code beside an ASI
loader that ReShade happens to share (`scripts\`): the loader keeps it, minus
ReShade's own files. Two different builds in folders of their own are versions
to pick from, when the drop isn't already asking about versions of its game
files; a picked version's own build wins over one it's laid over. Side by side,
or alongside game-file versions, nothing of ReShade goes in and it's said. A
second copy of the same build under another name, a readme and an empty marker
file stay out. Its record carries the preset and the menu key off its
settings, for the list's line, which says the build is missing if antivirus
takes it. In `ReShade\` only, a file already there with the same bytes is
taken on as its own, and settings there that nothing has a record of stay the
user's with the new ones beside them as `.new`, which is what lets it go back on
after a Remove left the settings ReShade had saved; beside the exe, settings on
no record are still refused, since they can be another mod's.

The patcher's own ReShade, the optional row Apply puts in, reserves those names only
while it's in. A drop with it in is turned down with a line saying to Remove it
under Installed first, since two ReShades in one folder is neither; with it out
the drop goes in as above. The other way round, Apply leaves the row out when a
`ReShade\d3d9.dll` that isn't its own is there, before writing anything.

NovaFix's "loaded add-ons" popup names each dll beside it by its version
resource and otherwise by its file, so HD Fonts and GUI's `version.dll` reads
`VERSION`. A mod's dll always goes in exactly as its author built it.

Build src_38 (`patcher/pe_name.py`'s `named()`) appended a section holding the
resource table plus a VS_VERSIONINFO to proxies with no name, and XIII-2 and
LR stopped starting with it. Any dll it renamed is restored the next time the
Mods list is read or the game is launched from here, with the game closed
(`unnamed()`, through `loader_mods.rewrite`). The section named() added is taken off and the four
fields it wrote over are put back: the section count, the image size, the
resource table's entry and the checksum. Which values they had is found by
hashing each way they could have been against the digests on the mod's record,
the file's length included, since named() padded the file out to the alignment
with zeros and GNU ld's builds end past it. Only a match goes in. A dll that went
in already renamed has no original on record: one a linker left on the
alignment with no checksum goes back when naming it again gives exactly what's
there, and anything less certain stays as it is. Both versions stay on the
record, so Remove takes off either.

The list's line under a loader is `Loads as <dll>`, which is where the mod
loads from.

## FOMOD installers

A Nexus mod that comes in options often says which with `fomod/ModuleConfig.xml`
(`patcher/mods/fomod.py`): files that always go in, then steps of groups of
plugins, each plugin folders and files mapped to where they go, and flags its
picks set that turn more files on further down. Vortex and MO2 put the
questions up in a wizard; here each is the drawer's version question, a group
at a time, with what each option is said under it and the one the mod
recommends picked to start with. Install reads Next while another could
follow. A group where one or none is picked is one question, and one where any
number can be is a yes or no for each plugin, since the question takes one
answer. Required plugins go in and ones marked not usable stay out without
asking, a group of one required plugin or a SelectAll asks nothing, and a step
the flags so far hide is never asked.

The answers so far are kept by the download they're for, so each Install is
the answer to the last question; a drop with no answer starts over. Once
they're all in, what they come to (`fomod.installed`, a higher priority
winning a path and the later of equals, the way the installers do it) is the
drop from then on, each file under the path it'd have been installed to and
nothing the answers left out, read like any other: into the archives, beside
the exe, loose on disk. What they come to can still have versions of its own,
asked after the last answer, which is kept for it. Each file's record names it
the way the download does, so a copy a repack lost can still be read back out
of it. Before this, a FOMOD went in as every option's files at once wherever
they didn't clash. The config is read in UTF-16 or UTF-8, and one that won't
read says so.

## Loose files

Some of what the game reads isn't in its archives: a movie in its `movie`
folder, LR's `dlc` folder, whatever else sits loose under the data folder
(`patcher/mods/disk_files.py`). A file that isn't in any index goes there when
the longest tail of its path, a folder and the file at least, is one file in
the game's folder and no other ends the same way, or when it's laid out from
the data folder into a folder the game has. Never one of `sys`'s indexes or
archives, which go in record by record ([Whole archives](#whole-archives)),
nothing in a folder with an exe in it, or NovaFix's add-on and ReShade folders
beside one, which are the loaders' and Apply's, and never a file a loader mod
holds, LR's HD Fonts and GUI's data inside `weiss_data` say.

A zone's archive (`zone/white_z0120a_img.win32.bin`) is loose on disk with its
index inside `white_img`, and nothing of the drawer's goes into one, so on LR a
mod that swaps it whole, a music volume one say, is a loose file like the rest,
copied in a megabyte at a time rather than read whole since one is a gigabyte
or two. Its copy is held against the index the game keeps for that zone before
anything moves (`hd_zones.zone_problem`, the check the HD install uses), so one
that's cut short or laid out differently, which the game would crash reading,
is said instead; so is one that brings its own index, which goes inside
`white_img` where the drawer doesn't put zone indexes. On XIII and XIII-2 the HD
Project builds its art into those archives and backs up whatever's there first,
so one swapped in under it would become what its backup calls vanilla: there
they stay the HD Project's, and a whole one is said and left. A loader mod's own
data is on the disk once it's in, HD Fonts and GUI's `hd_textures` say, so
dropped again it's that loader's to claim, never loose files.

Each goes in over the game's file with the original moved into the undo store,
on the same drive, so it's a rename rather than a copy of a big movie. Every
file is on the record, digest and all, before anything of it is on the disk,
so a crash part way leaves a record still marked pending that the next install,
list or Remove finishes undoing. Remove puts the original back while the file is
still what went in; one changed since, a Steam verify say, stays as it is with
the original kept, and the row says so. A new version of a mod comes off whole
first, so its record keeps the game's originals and never its own last version.
Two mods can't both be one file, the rule the loaders have. A mod whose loose
files went in beside its archives' or its loader's is one row under the name
those went in by, "Pack (2)" when another mod had the name, and they come off
with it. One that's only loose files is named the way a loader mod is: an
update when it shares a file with the one by its name, else another mod, and
the other kinds count it the same way, so a costume dropped by a movie mod's
name goes in as "Opening (2)" rather than joining its row. A patcher's DLC
file or a loader's going over one names the loose-file mod that has it.

An xdelta patch for one of those (`patcher/mods/deltas.py`) is decoded against
the game's copy a window at a time into a file in the undo store, every window
checked against the adler32 xdelta3 keeps, so one made from another version of
the file says so instead of writing noise over it, and what it makes goes in
like any loose file, the decoded copy cleared after. Room for that copy and the
one going in is checked first, and a drive that fills anyway is said as that.
Its file has to be one a loose file could go over, so one for Apply's
ReShade.ini, a loader's folder or the archives in `sys` is said, not decoded. Which file it's for is the name
xdelta3 wrote in its own header, else its own name less the `.xdelta`. An index
and archive pair patched together, named the Console Content Patch's way or not,
go through [the .bat's](#a-mods-bat) record by record decode with no `.bat`
round them. One for the exe, half a pair, or a file the game hasn't got is said.

## Records inside a container

LR keeps most of its little files in `db/resident/wdbpack.bin`, a WPD, and its
`.wdb` tables are WPDs too. WPDtool unpacks one into a folder named `_` and its
name, so a mod that changes a record ships it that way:
`Data/db/resident/_wdbpack.bin/r_treasurebox.wdb`, the way the LR randomizer's
Nova packs come. `look()` matches the container's path like any file's
(`_in_container`), so whatever's in front of it doesn't matter and `EN-Data`
still says the voice set, and `patchdata.lay()` builds each container the way
[LR's patchers](#lrs-patchers) are built: out of the archive it's read from as
the game has it now, mods and all, those records swapped. It goes in with the
drop's whole files as one mod, `records` on its record. Only into the archive it
was built out of: `pairs_for` keeps another of the voice set listing the same
path out, as `only` does for a whole build. Two different files for one record
are said and nothing moves.

A mod with records is built again when one under it comes off, the way the
patchers are, and that now keeps the files it has whole too (`captured()` holds
them as they are), which before went back on without them. Back on, it goes on
top, so when another mod went in over one of those whole files the Remove is
turned away until that one's off, rather than leaving it underneath. A new
version of a mod with records takes the old one off first, the way Remove
would: its containers are built over the game as it is, and built over the old
version they'd keep whatever records the new one drops. That's said as having
happened only once it has, so an archive Steam has open refuses with nothing
changed and nothing claimed.

## Whole archives

Some mods ship an index and its archive whole and say to drag them over the
game's (`filelistu.win32.bin` and `white_imgu.win32.bin` into `sys`). Swapped
in, that would throw away everything the HD Project and the drawer put in those
archives, and a Steam update would take the mod with it. So
`recipes.whole()` reads it the way `restore()` reads an xdelta pair: every
record of theirs against Steam's under the same code (the HD backup's index
when there is one, else the index as the drawer's first mod found it, else the
live one with what's under the drawer's mods), and only the ones whose bytes
differ go in, each through the same [record by record](#a-mods-bat) install.
One their tool deflated again, the same file underneath, isn't a change, and
wouldn't go over the drawer's mod for that file. Their archive is read through
a file to seek in (`Source.opened`), not read whole: its stretch of a zip when
it's stored there, else copied out beside the drop once. What differs goes onto
the disk record by record as it's found, so a whole archive's worth of changes
never sits in memory.

Half a pair is said, the index being what says what's in the archive. One
listing files Steam's doesn't was made for another version of the game, and
one holding nothing Steam's doesn't has nothing to put in; both are said and
nothing moves.

## Patchers, modpacks and blocked files

A patcher that carries its payload as a zip, like Red Rose Beachwear's
`PatchData.zip` beside `FFXIII2WhiteIMGPatcher.exe`, has that zip read as part
of the drop: its `chr/pc/c159/...` files go in like any other, and the patcher
isn't needed. Its `.dll`s are .NET libraries for the patcher (they carry a CLR
header), which is said instead of being handed to NovaFix. A zip inside a
folder is read where it is, and so is one stored whole inside a zip, which is
how a zip of a zip usually comes: its bytes are a stretch of the drop, read
through a window onto it. One that's compressed in there is copied out beside
the drop once to be read, since a member can't be sought through without
inflating it from the start every time. That's checked for room first, what
it unpacks to and a little more, and a drive that fills says which drive, the
drop's, not the game's.

Nova Chrysalia's modpacks are a zip with a `modconfig.ini` naming the mod and
its game. That name is the mod's, a modpack for another game is sent there
(Nova itself turns down a pack whose `GameEntry` isn't the game it's
installing to, so one that works there names the right game), and its folders
say which voice set a file is for. Nova installs by unpacking
the whole game and running the game off loose files; here they go into the
archives the same way as anything else. Nova isn't needed for any of it, the
two parts it did itself included:

- `External\` installers are played on a copy of the game the way Nova runs
  them: the one Nova picks (`Setup.exe` over `Setup.bat` over `Install.exe`
  over `Install.bat`), started in its own folder, with `whitepath.txt` beside
  it and the pack's `Data\` beside that, made if it has none. The copy has
  the game unpacked under that path, every record of the archives a file at
  its place and the pack's `Data\` already over them the way Nova leaves it,
  so an installer that checks for one or edits one in place finds it. What it
  changed there and what it left in `Data\` go in, `Data\` having the last
  word where both put a file, which is Nova's order. A pack whose
  `modconfig.ini` says `ExtPatch = false` doesn't have it run, the way Nova
  wouldn't.
- `Code\*.nccp` code patches are applied in memory as the game starts by a
  NovaFix add-on of ours, `addon-src/ff13-code-patches`, which reads them the
  way Nova's launcher did. Each is checked first against this game's exe (one
  past its end is for another game or exe) and against every other mod's
  (two writing different bytes to one spot can't both be in). They go in as a
  part of the mod's files, one Remove for both, or on their own row when
  they're all it has. See `patcher/mods/nccp.py` for why it's in memory and
  not the exe on disk.

Nova's `Datasheet` (its `.db3` editor) is how a modder makes a `.wdb`, not how
a pack installs one: packs ship the `.wdb` itself or its records, which go in
like any.

Windows Security refuses a file it calls a threat with its own error (225 or
226). That's reported as a block, not a damaged file, wherever it happens: opening the drop, opening a zip inside it, reading a member, staging
an add-on or writing a loader. Proxy `.dll` mods are flagged often, so the message says to allow it
under Protection history only if the mod is trusted.

## Putting a mod in

Nothing is written over. Each file goes on the end of the archive, padded to
the 2048 byte sectors the index counts in, and the entry is pointed at it with
`filelist.relocate_many`, which moves every copy of a path the index lists more
than once. The original stays where it was, under the mod.

In order:

1. every index it'll touch has to rebuild to its own bytes exactly
   (`hd_mirror._index_problem`), or nothing happens
2. space for what will actually go on the end, a write probe, nothing holding
   an archive open
3. what's there now is read in archive order and hashed. In mod order that's
   a hard drive seeking all over a few gigabytes
4. each file goes on the end once per archive that lists it, compressed if the
   original was, raw where deflate doesn't win. A file byte for byte what the
   game has already is skipped, unless another mod put it there, in which case
   this one stacks on that one without a second copy
5. the tail is fsynced, each index's old bytes are saved, then the record, then
   whatever sits on a file an update takes over, moved onto this record
6. the indexes are swapped in, then every moved entry is read back and hashed
7. only then does the version going out let go of what changed hands

Any failure before the record puts the tails back to their old length; any
after it puts the old indexes back too. A crash between the record and a swap
leaves a record whose Remove finishes the job.

The record is `.patcher-undo/files/<stamp>-<name>.json`. Each entry has the
hash of the mod's file (`sha`), the hash of what it went over (`under`), which
mod that was (`below`, by record id, empty for the game's own) and where both
sat (`was`, `now`, and a few older places in `seen`). Places are only where to
look first. An HD repack moves every file in an archive, so which mod is on
which is kept as names, and what anything is, as hashes.

A mod is named after its archive with the archive and any version taken off
(`Serah Dress-55-1-2-1699999999.zip` is Serah Dress), a folder's name as it is,
and a loose file's whole name, so a loose `.trb` and its `.imgb` are two mods
rather than one replacing the other. A second download (`Pack (1).zip`) or a
copied folder (`Pack - Copy`) is the same name. Several things dropped at once
go in one after another.

An update is a drop by the same name that shares a file with what's in. The
new version goes in on top and the old one comes off after, so a failed update
leaves the old one in. A file the new version shares with the old one changes
owner where it sits instead of going on the end a second time, wherever a
repack has moved it, so a recolor on a texture the update didn't change stays
on top, and whatever sat on it, a mod kept for a restore included, sits on the
new version from then on. One that ships part of a model, only the `.imgb`
that changed say, keeps the last version's `.trb`, the way unpacking it over
the old one would, even with another mod's file on top of that, and remembers
which download it came out of. A drop with the other file somewhere it isn't
going in from is half a model and refused, unless that's the very file the last
version has. Every mod by the name goes: two by one name is an update whose old
version didn't all come off, and what's left of it may share nothing with the
new one.

A drop that only shares the name is another mod, and goes in as "Name (2)". A
later drop by that name sharing its files updates that one, and one sharing
files with both goes to the one dropped from the same place, or failing that
the one it has more files of.

An update that dies after its record is written has both versions listing what
changes hands, the same file in the same place, until the old one lets go. The
newer one counts and whatever sat on the old one sits on it, and whichever of
the two comes off first leaves the file to the other rather than putting back
what was under it. A drop of that update again moves whatever sits on either.
The old version is marked `replaced` in the write where it lets go, since
what's left of it is half a model: if taking the rest off fails then, the next
install, Remove or settle finishes it, and says nothing, the new version being
in.

Whose file the game holds at a path is the highest entry on its stack that
hashes to it. Anything above that isn't in the game any more, left on record by
a restore or by a Remove that stopped half way, and a mod dropped on top of it
crosses it off first, so the stack stays one stack.

Every hash the drawer works out is kept against the archive's identity, size
and mtime, and carried over its own appends and cut backs, which leave what's
before them alone. Opening the drawer after an install doesn't hash every mod
again, and neither does the first look after the app starts: the hashes are
written beside the records (`.patcher-undo/files/contents.cache`) after each
install, Remove, settle and listing that found anything new, and read back for
each archive still exactly the file it was. An archive written to since, by
anything, reads as new and is hashed again; one written inside at its own size,
the way Leviathan's Tears went in, drops the file on disk too. A damaged cache costs
one slow look, never a wrong one.

## Taking one off

Each entry sits on the one its `below` names. Whatever names this mod as what's
under it, a later mod or one taken off since and kept for a restore, is handed
what was under this one first, so the stack closes over the gap whichever
order things come off in. It says who handed it down (`from`): until this
mod's record is gone both sit on the same thing, and a Remove cut short there,
or one that ends stuck, still reads with the one handed down on top. One with
the very same bytes only leaves them to the mod it was handed down from while
the game still holds another file of that model of that mod's.

Then, if the index is still byte for byte what this mod wrote, its saved copy
goes back and the archive is cut back to its old length. Otherwise it goes
entry by entry, only where this mod is the top of its stack and the game holds
its file, wherever a repack carried it. Where a sector sits isn't proof of
whose file is in it, which is why everything that decides goes by the hash.

The HD installs repack, and a repack keeps whatever the index points at and
drops everything else, the originals under a mod included. So what was under
is looked for by its hash: in the live archive at every place on record and
where the game's entry points now (a mod on top of the very same bytes is
holding them, wherever a repack put them), then in the HD backup's copy, which
has everything where it was when that was taken, then in the mod's own
download, or the one an update took the file over from, if it's still where it
was dropped from (a folder, a zip or a tar: a `.7z` or `.rar` would mean
unpacking it again). What turns up outside the live archive goes on the end.
Like an install, a Remove refuses before anything moves while the game or Steam
has an archive open, and before that, while the game or Square Enix's launcher
is running out of the game's folder at all.

A mod further down whose file is nowhere any more is passed over for what was
under it, and comes off whole: a costume missing its `.imgb` is a `.trb`
pointing at textures that aren't its own, which is worse than no costume. The
drawer names the ones that went. The other voice set's archive is looked in
too, by hash: a model is the same file in both, and one set's original can
outlive a repack of the other, so a voice set with a file that has nothing to
go back to waits for the others to come off first, once. Only with nothing
anywhere is a file stuck, and then the Remove still finishes: that file stays
as the mod left it, the other half of its model with it (a `.trb` and its
`.imgb` only load as a pair), the mod comes off the list, and the line says
which files stayed and that Verify Integrity puts them back. A Remove that
failed every time was a row nobody could get rid of.

All of that is written down before the index moves: the mods passed over go on
the record of the one coming off, in the same write as where its entries sit
now, and each passed-over entry is marked `lost`. A crash anywhere after leaves
something the next Remove, install or settle finishes; whichever runs next
takes any mod marked `lost` the rest of the way off.

A mod taken off after the HD backup was taken is still in that backup, so a
restore would put it back. Its record is kept, marked off and out of the list,
its bytes stay where they are so nothing else lands there, and
`game_files.settle()` takes it off again whenever a restore or an HD run brings
it back, onto the highest mod on record under it whose file can be had, which
may be one that went in after it came off. It goes before the rest: what it
covers can only be judged once it's off again. A mod that went in after the
backup comes out with a restore, and settle drops its record; an HD install,
repair or Revert lifts every drawer mod's own bytes out of the game first
(`game_files.lift`, into the undo store's `work\`) and lays the ones that
came out back on top through the drawer's own door afterwards, oldest first,
so the stack is the one the person built and Remove still goes back to
what's under each (`lay_back`). Only one it couldn't read whole beforehand is
left out, with the message to drop it again; one the restore took half of comes off the rest of the way, and so
does one left on record under another with its own file nowhere, since what's
really under that one now is whatever was when the backup was taken. One out
altogether that the backup has whole is a Verify's doing, and stays on the
list, since the next restore brings it back. Settle goes round again while a
pass changes something, since taking one off can put back a file of one it
already looked at, and says what it couldn't do. A mod kept off that wouldn't
come off again still covers what it covers, so nothing under it is judged
until a later settle gets it off. After an HD run it also records where a
repack carried each mod, before the copy into the other voice set reads where
they are.

`--revert` takes every one of them off first, newest first, then does the
rest, then has another go at anything an HD restore made removable.

## HD Models Plus

Its main download isn't files to put in: `_FF XIII HD Models+ Install.bat`
is menus around one tool, `SupportFiles\FF XIII HD Models.exe` (4.1.7), which
reads a recipe out of `Data\`, the game's own models out of `models\orig\`,
and writes new ones into `models\mod\`. Their `.bat` then imports those into
both voice sets' scr pair with `ff13tool -i`. Its setup copies both scr files
out and unpacks them whole to fill `models\orig`, and turns away a game whose
six aren't the sizes Steam ships unless you type in the HD Project's folder.

`patcher/mods/models_plus.py` does all of it, the tool included: its model
building is ported to Python in `patcher/mods/hdmodels/` (below), so nothing
of theirs runs and no Windows is needed for it:

1. a drop with its `.bat` or `SupportFiles\FFXIIItrbSetup.bat` in it is its
   main download (its Nova Chrysalia versions don't have either). It's XIII's
   only, said on another game. It's under XIII's Worth getting too, where Open
   page watches Downloads for its zip (an 83 MB file, so the watch's floor is a
   tenth of that) and what lands goes in like this drop. With its build in,
   that row reads Installed: `bridge.file_mods` tags the drawer's record
   with the row's job by the name both go by, and only a record with one of
   its versions on it, so a costume that only shares the name isn't it
2. its versions are offered as a choice under its Worth getting row, the way
   the HD Project's categories open under its own, each with a line saying
   what it is and one Install naming the pick: Ultimate (recommended, and
   picked to start with), Ultimate with Lightning's HQ hair, and High
   Quality. The answer carries the row's job and the recommended one, so the
   page doesn't guess either. The question stays up until the build is in, so
   one turned away while an HD install, a patch run or another mod is going is
   pressed again rather than dropped again, and each game keeps its own.
   Lightning's normal hair unless asked, since the HQ one is rigged
   differently and holds still in some in-game cutscenes. Each is the list
   of recipes `_All HQ.bat` runs for it, in its order
3. the originals the recipes name (their headers list them) are read straight
   out of the archives, a half at a time and each the size Steam ships it at.
   In each voice set, a half a drawer mod holds comes from under that mod
   (found the way Remove finds it) before the HD backup, since a backup taken
   after the mod went in holds the mod; any other half comes from the game
   before the backup, since a backup can hold a mod taken off since. The
   backup is for what the HD Project replaced, which is never Steam's size.
   Then the drop's own `models\orig` from an earlier run of their `.bat`,
   then the HD Project's. A recolor holding only the `.imgb` leaves the `.trb`
   to the game. Nowhere to find one and nothing is built, with the person
   named and what would put the original back
4. each recipe in the version's list is built natively in the `.bat`'s
   order, reading `Data\` and `Textures\` straight out of the download and
   the originals read above, into a folder of ours,
   `.patcher-undo\work\models-plus-*`. A file in the drop whose name climbs
   out of the download refuses the whole drop, since `Data\..\..\..\..` is
   the game. A recipe the builder can't build stops it with nothing changed
   and says whose model and why in a few words; Stop is heard between edits.
   A folder a crash left is swept by the next build. The drawer's line says
   Building with a percent: each recipe is an equal share, and reading the
   originals counts as one more
5. what it built goes in through the door above, as one mod called
   HD Models Plus with its version on the record, both voice sets. Over the
   HD Project's characters it stacks like any mod, and Remove puts the HD
   Project's back; picking another version later is an update of it, so a
   field model the new one doesn't build comes off with the old one. The
   build folder goes either way, and the line says Installing with a percent
   of its own, the way any drawer mod's does

### The builder

`patcher/mods/hdmodels/` is FF XIII HD Models.exe 4.1.7 ported from its
disassembly, byte for byte: the container and its name swaps
(`build.py`), the recipe reader and its `::` directives (`recipe.py`),
textures and HD Models Plus's scrambled `.bin`s (`texture.py`, `texedit.py`),
the skeleton (`skeleton.py`), `elb` and `msst` blocks, and every mesh edit
(`wrb.py` and the `mesh_*.py` beside it, each registered by the exe address
it was ported from). The exe's own memory is modelled where its editors walk
raw pointers (`cmem.py`, `heap.py`), so a ported line reads like the code it
came from, its float rounding and its bugs included.

Checked against the real exe under wine, on the real game models:
- all 40 of HD Models Plus's recipes, both answers to any question they
  ask, 48 runs, every output file the same;
- every edit on its own over six real models, a few thousand cases;
- the Vanille Nude add-on's recipe across its sliders and questions.

The one difference: where the last mip of a texture is shorter in the file
than its header says, the exe writes whatever was in its heap into the
padding behind it, up to 15 bytes, and this writes zeros. The game never
reads them. Where the exe would read memory it doesn't own (an index past a
mesh, a bone list it overran), this refuses the recipe with a short reason
rather than guess. A recipe asking for a newer exe than 4.1.7 (version over
52) is turned away the same way.

It's pure Python, so it's slower than the exe: the Ultimate version's
eleven recipes take a few minutes on a desktop, most of it the imports that
grow a mesh a vertex at a time. `wrb.parse` keeps that down by refilling its
arrays in place, moving only what a resize moved, and during an import
leaving the untouched meshes' moves until the next edit can read them.

## HD Models Plus's add-ons

Mods built on HD Models Plus (the Vanille Nude Mod is the one seen) ship a
`.bat` that calls `SupportFiles\FF XIII HD Models.exe` and `ff13tool -i`, and
expect HD Models Plus unpacked under them. The drawer plays that `.bat` like
any other (see below), and in the play:

- `FF XIII HD Models.exe` is the builder above: the same command line
  (`-s`, `-o N`, `\V`), the same exit codes, its `::A` questions asked like
  the `.bat`'s own, its models written into `models\mod\` for the `.bat`'s
  `ff13tool -i`;
- a `SupportFiles\` file the add-on expects from HD Models Plus
  (`FFXIIItrbSetup.bat`, `ff13tool.exe`, the exe itself) is lent from a kit
  built in, so nothing needs downloading first, and the setup's
  `models\orig\` is the game's originals read the way the main download's
  are, never an unpack of the archives;
- what goes wrong is said shortly, as the patches are: "builds a character
  model, and its recipe asks for block 9 of a model that hasn't got one".

The sliders a `.bat` asks with `CHOICE` take the middle answer first (5 is
the add-on's standard).

## A mod's .bat

Some mods are a `.bat` around their author's tools. The Console Content Patch
moves XIII-2's archives aside, runs xdelta over them to put the 1.0 release's
cut costumes back, then injects two files with Cocoon. Running that is
somebody's exe over the archives with no record of what was under, and xdelta
writes the whole 1.5GB archive out again in the Steam layout, taking whatever
the HD Project or the drawer had appended with it. So `patcher/mods/recipes.py`
reads the `.bat` instead, following every copy, move and delete on paper, and
what ends up in the archives comes out as two steps the drawer does safely: the
records an xdelta patch really changes, decoded out of it, and the files Cocoon
injects.

A menu is a choice per option. Options putting different files in one place
are versions of the mod and are asked about; the rest are steps done in the
menu's order, which is how "revert to 1.0 first" reads. An option that does
anything else to the game's files, downloads, or runs another tool is left out
with the reason. The Console Content Patch's update option is one.

`patcher/mods/vcdiff.py` reads VCDIFF the way xdelta3 writes it, a window at a
time, and never builds a patched archive whole. Its copies say which target
bytes are copied straight from the source, so a record whose bytes come from
where they were is one the patch didn't touch, and one copied from somewhere
else holding the same bytes, the duplicate Steam keeps of c201 and c204, isn't
a change either: over a costume from the drawer it'd put the game's back. The
source is Steam's archive: the HD backup's copy when there is one, since an HD
install repacks the live archive, else the live one, with what the drawer's own
mods hold read as what's under them. The index the patch was made from is the
one the first of the drawer's mods on it found and kept, then the HD backup's,
then the game's own, since the drawer rewriting the index changes what an
xdelta of it copies. Every window is checked against the adler32 xdelta3
stores with it, which is also how the right one of those is told, so a game
that isn't what the patch was made from says so instead of turning into
costumes made of somebody else's bytes. What it puts back goes into the
indexes the `.bat` changes and no other that lists the same path.

Steam's update renamed 30 of the cut costumes' records to copies of c201's and
c204's, so their own paths aren't in the index to match. Those go in by code: a
record given its 1.0 name back is on the mod's record with the name it had, and
Remove names it back. Two of them are placeholders in 1.0, 0 bytes stored as an
empty deflate stream, and go in as one. Worth getting's DLC Restoration on
XIII-2 knows it's in by the record's `recipe`, the `.bat`'s name, and its Open
page watches Downloads for PCGamingWiki's file by name, since only Nexus puts a
mod's id in its downloads.

## Any other installer .bat

The rest of the `.bat` mods are played, all but one whose name says it takes
a mod back off (`Uninstall Patch.bat`) and not that it installs too. Names
don't say much more than that, `Remove Censorship.bat` and `Disable Intro
Video.bat` being mods, so the rest is told by what a `.bat` writes, looked
ahead at before anything of theirs runs. A file of the mod's, a record, the
work of a program of theirs, or one of the game's files or records copied
over another (Lightning's model in Vanille's place, a short movie over the
opening, both swaps) is an install; one of the game's files deleted can't go
in, and is said. A file of the mod's with the bytes that are there already is
the mod in already, dropped again or put in by hand before: the answer that
wrote it is still the one given, nothing else of the drop goes in in its
place, and the drawer says it's in. Dropped again after the drawer put it
in, its record says what each of the mod's files went in over, so a play that
puts that back, or takes away a file the mod added, is its uninstall however
the `.bat` offers it (a menu, a typed word, a yes or no, or without asking)
and never an install. With all of it still in the game and nothing the
`.bat` does putting anything new in, it's in already; after Steam's verify
put the game's own files back, or with a newer version dropped, the answers
that put it in are looked for as they were the first time. Originals put back
aren't an install either: a backup of the game's file (`op.win32.wmp.bak`,
`backup\op.win32.wmp`), or a file out of a folder of originals (`originals\`,
`Backup Files\`, `Stock\`, not counting the folders the `.bat` itself is in)
that sits beside another of the drop's files by that name or, on a game the
drawer hasn't put the mod in, has the bytes the game has there. A `.bat` that
only does that is the mod's uninstaller, passed over with the drop's files
going in themselves. `Vanilla\` is as often the version for an unmodded game,
and `Default\` a version, so neither is a folder of originals. Among several
`.bat`s, one with an undo in its name comes after one without, one for a step
of the work or another program (`Extract.bat`, `Install ReShade.bat`) after
that, one that does nothing but start the game, Steam, a readme (quoted names
with spaces too), a folder or a web page, or only says things
(`How to Install.bat`), comes last, and the likeliest is the one played; one
further down is never played in its place, since next to an installer that
won't run twice it's as likely the Restore.bat. Two that turn the same thing
on and off (`Enable Intro.bat`, `Disable Intro.bat`) come back as versions,
since only the drop's name says which half is the mod. `patcher/mods/batch.py` is
cmd.exe's language, parsed the way cmd parses it rather than the way its help
says:
`%var%` expanded a line at a time as it's read and a whole `( )` block at once,
`!var!` and FOR's `%%a` per command as it runs, IF and FOR taking the rest of
their line, `&` and all, as their command, goto looking forward and wrapping
once, `call :label` as a new frame with its own implicit endlocal, and a
syntax error ending the script the way it ends cmd's. The built-ins installers
lean on are all there, `copy` with cmd's wildcard target names (`*u_orig.bin`
is how MJB names his zone backups), `xcopy` and its file-or-folder question,
`robocopy`'s `/XN /XO /XC` and `/e /xf *`, `set /a`, `choice`, `set /p`,
`for /f` with `usebackq` and `tokens=2*`. MJB's whole XIII-2 HD installer plays
through it end to end, drive scan and all.

What it plays against is `patcher/mods/sandbox.py`'s PC, not this one:

- The game sits at `C:\Program Files (x86)\Steam\steamapps\common\<its folder>`,
  every file a reference to the real one. That's the first place installers
  look, the drive scan's one drive finds it, the registry's Steam keys and
  `libraryfolders.vdf` and the app manifest all point there, and a folder
  picker (MJB's `FFXIIIGetPath`, PowerShell's `BrowseForFolder`) is shown it.
  On a default Steam install that's the real game's path too, which is why a
  program of its own is handed the stage's paths instead (below), and why the
  game folder is compared before and after: a path the script writes into a
  file for a program to read can't be swapped.
- The drop is in `Downloads\<the mod>`, the way unzipping it leaves it. A
  `.bat` that only reaches the game by `white_data\...` with nothing to find
  it by is meant to be run from inside the game's folder, so its folder goes
  in there; by `..\white_data\...`, a folder of its own inside it. What that unpacks anywhere inside the game's own folders, or over
  its files, is the mod and is there before the `.bat` runs; the `.bat` and
  its tools sitting beside the game are only the installer's. So is what of
  that it put to use, a file it copied over one of the game's, joined or
  injected, a version's payload beside the one it took (the same path but for
  one folder), and a folder of originals it keeps for its uninstall
  (`originals\`, `backup\`). The rest, a loader's settings say, is the mod's.
- 7-Zip and WinRAR are installed where their installers put them
  (`C:\Program Files\7-Zip\7z.exe`, `C:\Program Files\WinRAR\`), registry
  keys and all, so a `.bat` that checks for one before it unpacks anything
  finds it. The checks for admin say yes (`net session`, `reg query
  HKU\S-1-5-19`, `whoami /groups`, `fsutil`, `openfiles`, `cacls`, by name or
  by their full path in `System32`), and `ver` answers as Windows 10, until a
  script that gave up on one of those is played again the way it wanted. The
  stand-ins themselves are never run. An archive the drawer couldn't open
  (a zip inside the drop locked with a password or packed so only 7-Zip
  reads it is one of those) goes to a 7-Zip or UnRAR the drop carries that
  reads that kind (7zr only .7z, 7za everything but .rar, UnRAR only .rar),
  the one called what the script ran first; without one, a zip is
  unpacked here a member at a time, straight into the store and once per
  install, with the password a `-p` gives it, and what that can't read (a
  .7z, Deflate64, PPMd, AES) goes to this PC's own 7-Zip when it has one.
  `*.zip` unpacks every one it matches. What's left is said: a password it
  doesn't give or one that doesn't fit, 7-Zip this PC hasn't got, no room on
  the drive, damage. A zip split in parts (`pack.z01` beside `pack.zip`, or
  `pack.zip.001`) goes to this PC's 7-Zip with its parts beside it. A drop
  that's itself a locked zip, or one part of a split one, says to unpack it
  with 7-Zip. One that only locks its readme or packs a preview so only 7-Zip
  reads it goes in, and a file it needs that's locked, an archive inside it
  included, says which, in which zip.
- This PC's own Python, Java, Node and .NET, when it has them, sit where their
  installers put them (`C:\Program Files\Python312\python.exe`) and on the
  PATH, and run a script of the mod's on the stage like any program of its
  own. Asked only their version, they say one without being started. The
  `python.exe` Windows ships that only opens the Microsoft Store isn't one.
- ff13tool, ffxiiicrypt, xdelta, Cocoon, 7-Zip, WinRAR and the 4GB patchers
  are played too, on descriptions rather than bytes (WinRAR's, Rar's and
  UnRAR's `x` and `e` unpack what the drop carries the way 7-Zip's do, 7-Zip's
  `h` and `certutil -hashfile` hash the play's own copy of the file, the
  game's real one for a version check (the download's own 7-Zip hashes for
  real, and so does this PC's past what's modeled here), and either one run
  bare, testing, listing or packing a backup just passes): an
  unpack of `white_img` is a list of where each file sits in the game's
  archive, `-i` is a record swapped in the index and its bytes noted as
  appended to the archive, `-c` is an archive built from a folder. ff13tool's own output is said word for word, since
  MJB's scripts count its Added/Updated/Imported/Wrote lines. Whatever isn't
  one of those, the author's model builder, WhiteBinTools, a patcher of their
  own, runs for real in a stage under `.patcher-undo\work`, over copies of what
  it can see: its own folder and the one it runs in, of a whole unpacked
  archive only the folder of it it's handed or runs in (one character's,
  say) and the files named like one it's handed (a `.trb`'s `.imgb`), and
  none of the game's files unless an argument names them or it runs in the
  game's folder. Its command line
  has every path swapped for where that is on the stage, right where it sits,
  whether it's a word of its own, glued to a switch (`-oC:\out`,
  `"-oC:\out"`, `in=C:\x`, `-"C:\x"`), after an `@`, a `;` or a `,` in a list
  (`"C:\a","C:\b"`), or climbing with `..` (`key=..\..\x` keeps its key), and
  `/OUT:\x` is a switch with a path, not a drive T:. One from the root, what
  `%~p0` gives or `/Users/...`, counts when it starts in a folder the play has
  or would land in the real game from the drive the stage is on, so
  `\nfullscreen=0` stays an escape, and so does a new folder at the root,
  `\ModOut`, which goes as written. A path left bare gets quotes when the
  stage's has spaces. An output that isn't there yet, however many new
  folders down, is read back after. Its `TEMP` is the play's, with what the
  script left there, though past 256 MB of copies only what it's handed, runs
  in or has already goes over. What it writes there comes back in, and each
  voice set's play starts from fresh copies, so one's leftovers aren't the
  next one's.

A question gets the answer someone installing gives it: the automatic search,
the game's folder for a path (the one of its folders the script then checks
for files under), and otherwise the answer it says installs or carries on
(`Y to install`, `[I]nstall`, `Install the textures (Y) or skip them (N)`),
then one it says skips, and last one it says takes the mod back off
(`Y to remove the mod or N to cancel` is an N, `(R)estore original files` and
`un-install` read as that too). A bare yes or no, `(Y) Yes / (N) No`
included, is read off the question itself, or when all it asks is
`Continue?` or `(y/n)`, off the nearest question put up since the last one
was answered; a line that isn't a question (`To uninstall it, run
Uninstall.bat.`) says nothing about it. It's a no when it's about going back
to the game as it was, leaving part of it out or skipping (`Restore the
original opening instead of installing?`, `Keep the original intro?`,
`Replace the HD opening with the original one?`, `The opening only, without
the HD textures?`, `Skip the cutscenes? They're 6 GB`), and otherwise a yes, a
part asked about on top included (`Restore the cut content too?`, `Remove the
censorship as well?`, `Skip the opening logos too?`, `This removes the old
version first. Install?`). A script that only checks one way
(`if /i "%r%"=="n" goto skip`) gets the other answer as well. That's only the
order: a play that installs nothing is tried again with each question's other
answers, all of them, the last one being none of the values the script checks
it against, which is what gets past an `if /i "%ed%"=="gog" goto :eof` to the
rest of it. So a question read wrong costs a play and never the mod (`Restore
the original Japanese theme song?` from a mod that does just that is a no
first, and its yes is what goes in). When both ways of answering a yes or no
install, each is played and the one that puts in all the other does and more
is taken, so a part of the mod is never left out by a misread: `Skip the
summon animations too?` from a skip mod and `Also skip the 4K movies?` from an
HD pack read alike, and only what each answer puts in tells them apart. A
settings file the game has written over doesn't count as more, so `Keep your
current settings?` keeps them; two answers that put in different things
(`Low VRAM mode, 2K textures instead of 4K?`) get the one read as carrying
on. Each way of answering is played once. A menu of what to install comes back to the drawer
as its versions whichever of them got it installing, put up before another
question or right above it, a menu inside a menu as "Outfit, Hair",
uninstall and exit left off (a menu of nothing else gives its first, judged
by what it does like any play), and the one picked is what goes in. A menu
under that one that only a PC changed for the script reaches comes back to
be picked from next, with the first pick in front of each. Menus in menus
come back a question at a time the same way, up to four deep: BareLight's
`Nude or Default`, then `Boots or bare feet`, then the pouch, then the brand,
each a short list with what's picked so far said once on the drawer's line,
and a question with one answer left isn't asked. A `CHOICE` whose question
names each key, `Install (N)ude Lightning, or (D)efault Lightning?`, is a
menu as much as echoed `1.` lines are, unless it's about how to install (find
the game, carry on, take it back off, the voices). One called Default or
Original that writes what the game has now isn't offered, played for real when
a program of theirs hides that from the look-ahead, since picking it does
nothing; any other menu's answers are versions whatever's in. Back at
the menu after the pick went in,
it leaves by its Exit. Finding the answers and working the versions out are
counted in plays rather than seconds, so the list the drawer shows is the one
the install picks from on a slower PC too. Installers side by side under
names of their own, Gameplay Tweaks' `Mod_RunSpeed.bat` beside its
`Mod_Adamantite.bat`, are a pack of tweaks: picked the same way, but each goes
in as a mod of its own (`Gameplay Tweaks (RunSpeed)`), played over the game as
the ones before it left it, so the next one picked goes in beside it rather
than over it as its update, and "in already" is only ever said of the tweak
that is. The list stays up after each with the ones in ticked, each by what it
is (RunSpeed, not `Mod_RunSpeed`). A tweak's own pick between `.txt`s is
weighed by what each does to the game as it is: one that comes to what the
game has now is its "(O)riginal", never offered, and one that only puts the
game's own back is Remove's job, never offered either, so `+15% Run Speed or
(O)riginal` is no question at all and goes straight in. Picked anyway (a list
from before, say), the line says which it is rather than "in already". The one
in now is ticked among the rest. Each tweak's `.txt`s go on its record
(`wdb`), and one removed from under another that shares a table has the other
played again over what's left, its fields only, rather than handed a table
that still holds the removed one's edits (`wdbmod.replay`). A pair that are
really alternatives, `Install Long Hair.bat` and `Install Short Hair.bat`, read
the same, which comes to the later one on top and the earlier back when it
comes off. One `.bat` in folders side by side (`English Voices\Install.bat`
and `Japanese Voices\Install.bat`) is versions of one mod, named for its
folders, every tied one in each folder included (`Install (English)`,
`Install Extras (English)`). A
question whose answers are one English and one Japanese, any other a way out,
is the voices question, played once for each voice set the game has:
`(E)nglish or (J)apanese`, `(1) English or (2) Japanese voices`, `1 (English)
or 2 (Japanese)`, the two echoed above a `Choice:` with a `3. Exit`, `EN` or
`JP` typed at a voices prompt. A yes or no never is, since `Japanese voices?`
is as often a part of the mod and its yes goes in like any, and neither is a
menu of an English and a Japanese anything else (a theme song, a logo), which
comes back as versions. The same question asked over and over, a path it
never takes, ends the play and is said as that.

At the end, what the game's files were left as is laid out like a drop. A
changed index whose archive really holds what it points to becomes its
changed records, under the archive's unpack folder name so the voice set
reads off it, and only those that differ from the game. Anything else is a
loose file where the play left it, new folders and all, a program, a `.bat`
or a zip it put beside the game included. That goes in through the drawer's
own door, so it's on the Installed list as the mod's name and Remove takes it
out like any other. Backups it left (new copies of the game's own files, but
for a `.dll` or `.asi` a proxy may load, `d3d9_chain.dll`), what an archive was
unpacked into, logs, readmes and a game file written back with the bytes it
had don't go in; an index changed without its archive, a zone archive
changed, or a file deleted are said and left as they were. What it tried
outside the game's files, the registry or a download, is said as skipped. A
registry value it writes is there for the rest of the play, so one it reads
back finds it, and a key or value it deletes is gone when it checks (a `/va`
clears its values and keeps it); a script whose only work was the registry
says so. If
one of its programs got to the real game anyway, by a path written into a
file for it or by looking itself, and changed it, nothing goes in and the
drawer says which file, since that can't be taken back out from here.

Nothing about a play is real until the drawer puts it in, so a play that
stops (a missing program, a download, cmd giving up on a syntax error, a loop)
leaves the game as it was. One cmd stopped after it had done its work keeps
that work, the way it would on the PC.

## A .bat made for Nova's unpacked game

Nova Chrysalia unpacks the game's archives into loose folders under
`white_data`, and some mods' `.bat` is written for that: it checks for
`white_data\chr\pc\c201\bin\c201.win32.trb` loose and copies over it. A
play that wanted a file the archives hold, loose, is played again with the
game unpacked the same way, faked: every record a file at its place, read out
of the archive only if the script reads it (`Stage.unpack_game`, what a Nova
pack's External installer always gets). Nothing's unpacked on disk, and what
it wrote there goes into the archives like any drop, both voice sets.

A zip's empty folders are there for its `.bat` too: BareLight ships
`chr\pc\c201\bin\` empty to copy into, and checks it's there first
(`Source.folders`). And a `.bat` climbing to `..\white_data\sys\` reads as
one unzipped inside the game even when the file it checks is the voice set
this copy hasn't got, `filelist_scrc` beside the `filelist_scru` it has.

## A .bat that puts nothing in

A play that gets nowhere is read back for why (`patcher/mods/diagnose.py`), off
what the play kept as it went (`Run.trace`): every `IF EXIST`, `IF DEFINED` and
compare, every `&&` or `||` that sent it off, every program it ran with its exit
code, every copy of nothing, every page or file `start` opened. Walking back
from where it ended, the check that sent it off is the last one that gave up on
something and left on it, by a `goto` or an `exit`: a look for a file whose
miss leaves, a search whose find would have set a variable that never got set,
a command whose failing the line acted on (`reg query ... || exit /b`), a copy
of nothing. A check before a tidy-up, an optional folder, a look at one place
before the file turned up at another, and a drive scan or a look round other
Steam libraries that found the game all miss on the way to doing fine, so none
of them counts, and nor does one the script went past to a program of its own.
A program run on a check's own way off, a message box before its `exit`, is
that check giving up, and so is one on a compare that found its variable
empty (`if "%game%"==""` is `if not defined game`) or read the errorlevel of
a program that failed, whether it then exits, goes to a label at the end or
calls one that says so, unless what it ran there went on to the install (a
copy into the game, or a program handed one of the game's files): "install
it unless it's in already" is its work, where a log kept on the way out or a
message box shown the readme isn't. What a `call :label`'s own `%1` is checked against is
the call's, never something dragged onto the script, and an `%errorlevel%`
read after a call is the call's own.

When what it gave up on is something a PC could have had, the play's PC gets it
(`sandbox.Machine`, the mounts, the PATH) and the script is played again, up to
four changes (`ADAPTS`), after a look-ahead or, for a program of its own the
look-ahead couldn't see the work of, after playing it for real. A zip the
drawer never opened, one the script made under another name or one inside
another, is unpacked like any it did; only one that starts like a zip and
won't open as one, or an empty file or a page of text under its name (a web
page saved as the zip), is said as damaged, never a good .7z or .rar the
drawer had no way to open, nor anything else this PC's 7-Zip might read (a
raw .lzma, a part of a split archive). Each is said in the drawer's message once it goes in:
"Played its installer, install.bat, on a copy of the game (with the game at
D:\Games\FINAL FANTASY XIII too), and put in what it changed".

| What it gave up on | Played again |
|---|---|
| the game somewhere else: `C:\Program Files (x86)\Square Enix\...`, `D:\Games\...`, every place a search of its own looked, at a folder named the way the game's is (`FINAL FANTASY XIII`, `FFXIII`) or under a path ending in its exe; outside your own folders, a folder named for it with a word more (`FINAL FANTASY XIII (Steam)`) or one holding a file of the game's a few folders down. One in your own folders that isn't named for it, or one named for mods, a backup or a library (`FF13 Mod Library`), never is | with the game mounted there too |
| a registry key: the game's Square Enix entry, the .NET Framework's `NDP` key, the VC++ runtime's, a runtime's when this PC has that version of it (`PythonCore\2.7` never gets Python 3), by `reg query` or a `for /f` over one; Steam's own keys (`SteamPath`, `SteamExe`, its Apps key and uninstall entry for the game) are there from the start, and a value given later fills in beside them | with the key there, holding what its installer writes, for a key whose meaning is known (one nothing says the meaning of, a mod tool's own key with the game's name in it included, is never made up) |
| a file or folder of its download that isn't beside it: zipped a folder deeper, a search through `data\` and `files\`, a `robocopy` or `xcopy` from it | with it where it looked, when one of the drop's files, and only one, ends the same way |
| a file of the game's beside itself (`%~dp0FFXiiiLauncher.exe`) | from inside the game's folder |
| a file of the game's a folder or more up (`..\white_data\sys\filelist_scrc.win32.bin`), the readme's "unzip this into your game folder" | unzipped into a folder of its own inside the game's: the folders the zip has above the `.bat` (a zip laid out like `steamapps\common` included), then one named for the mod |
| one of the game's programs it wants copied in beside it (`%~dp0ffxiiiimg.exe`, a 4GB patcher's way), by the longest end of the path the game has, a lone name only when the game has the one file by it. Only an exe or dll: a movie or archive named like the game's is the mod's own, missing | with the game's own there |
| `%1` empty, or a variable set from it (`if "%~1"==""`, `if [%GAMEDIR%]==[]`, `%1%`, a usage line it falls through to) | with the game's folder dragged onto it, or its exe when the script reads `%1` like a file (`%~dp1`, `%~nx1`, an exe it names), or for one that copies from what it's given into the game, the download's folder that lands on the game's own files (`Textures\white_data` for a copy into `white_data`); a copy out of it into its own folder, TEMP or a backup means it's given the game. One that puts quotes of its own round `%1`, which cmd gives up on for any path with a space in it, gets the game dragged from a path with none |
| an admin check it leaves on passing (`Please don't run this as administrator`) | as a user without admin rights |
| a Windows version (`ver` piped into `find "6.1"`) | on that Windows |
| 7-Zip or WinRAR off the PATH (`where 7z`, a bare `7z x`) | with that folder on the PATH |
| a runtime at the path its installer uses, or its versioned registry key (`PythonCore\2.7`), this PC having that version of it: the program at that very path, the usual one when its major version matches, or that version's own install where its installer puts it (`C:\Python27` never gets a Python 3, but does get the Python 2.7 this PC has beside it) | with this PC's own there |
| a question that won't take Enter (`Enter your name:`) | with something typed |

What no change gets past is said as the cause, with what to do and the lines
that show it behind the drawer's (i), and doing it works on the next drop
without restarting the app: a runtime or 7-Zip that wasn't found is looked
for again, on the PATH as Windows has it now. A runtime this PC hasn't got (Python,
Java, Node, .NET, found by `where`, a run of it, its registry key or its
install path) and where to get it; a registry key held in a variable it never
set; a Python package and its `pip install`; a
program's exit code read the way Windows means it, `0xC0000135` a DLL that
isn't here, with the redistributable it comes with read off the program's own
imports (`MSVCP140.dll`, the Visual C++ 2015-2022 one), `0xC000007B` a 32 and
64-bit mix, .NET's launcher codes for the runtime it was built for and the
version it names, crashes, `9009`; a tool that stops to ask something nothing
here can answer (`Enter your license key:`), though a .NET one asking for any
key once it's done has finished, not failed; a download, PowerShell past an
`Expand-Archive`, an MSI or a script for Windows Script Host it hands the work
to; an archive of the download that's damaged, locked with a password it
doesn't give (or gives wrong), packed so only 7-Zip reads it on a PC without
7-Zip, or bigger than the drive has room for; a web page, a readme or a setup
it only opens; files it copies outside the game (AppData, My Games); a label
it goes to that isn't there; a loop waiting on a file, said by the file, the
same check a thousand times over with nothing changed in between, or ten
thousand times when all it changed was what it had changed before (a log line
or a count each pass), being a wait for something that never comes, while a
loop that puts a new file somewhere each time round is never cut short, a
label a FOR calls once an item is each item's own, and a play for real gets
three minutes of cmd's own going round, its programs' time (copies laid and
read back included) aside; a play one of those guards stopped puts nothing
in; a key of another of the three games (Square Enix's, Steam's uninstall
entry or its Apps key, piped into `find` or not), which says whose mod it is,
and the drawer puts none of its files in on their own; a game
version it checks one of the game's files for; a trainer that wants the game
running. Failing
those, the script's own words when they say what went wrong (`Game not
found`, `Set up the Mod Library tool first`), which go first over a path
nothing here knows the meaning of, never its goodbye (`Exiting...`, `Press
any key`) or a status line (`Installing, please wait...`), and last, what it
put on screen last. A tool that says `No errors found` or `0 failed` before
waiting for a key there's no keyboard for has finished, and so has one whose
last words are a note (`Nothing else to do`, `If the game doesn't start,
verify its files`, `Your saves will not be touched`), or with a `Done.` and
no `Press any key` after it when nothing it said failed and it left a file
with something in it; one that said what went wrong first (`This is not a
FINAL FANTASY XIII movie file`, `Finished with errors`, `0 files patched`)
failed, whatever it said after, and a yes or no is answered even after a
`Backup complete.`

## LR's patchers

`LRAerithGrabRestoration.exe` and `LROuterworldAddons.exe` are one program by
Surihix around two different `PatchData.bin` files. Run, it asks which voice
set you play, unpacks `db/resident/wdbpack.bin` out of that set's `white_img2`,
unpacks that with WPDtool, drops PatchData's files over what came out, packs it
all up again and repacks the archive in place, then copies PatchData's DLC
files into `weiss_data/dlc/0000003`. `PatchData.bin` is a zip laid out the way
WPDtool unpacks, so it says all of that itself (`patcher/mods/patchdata.py`):
`db/resident/_wdbpack.bin/r_dlc.wdb` is a record of `wdbpack.bin`,
`db/resident/_wdbpack.bin/_r_reward.wdb/rw_102` a record of a record, and
`a/dlc/...` a file for the English voice set, `v` the Japanese, `j` the
Japanese release.

So the container comes out of each voice set's `white_img2` the way the game
has it now, gets those records swapped (`patcher/mods/wpd.py`, WPDtool's layout:
records in table order, each padded to 4), and goes in as a drawer mod, in
every voice set rather than the one a prompt asked about. The DLC files for the
voice sets the game has go beside it as files on disk, through the loader door,
as a part of that mod: not listed on their own, and off with it. They go in
first, since DLC files nothing lists do no harm and the game listing DLC it
hasn't got does, and a first install that stops after them takes them back
off. Steam's LR has the `0000003` folder and nothing in it, so nothing is set
aside. Checked against the patchers themselves under mono, their own
`InitiatePatching` run on a stand-in game: `wdbpack.bin` and the DLC files come
out byte for byte the same, both voice sets, both orders. A game with both a
`v` set and an unlettered one, two Japanese sets under one voice letter to the
drawer, is turned away rather than given one container built from either.

The two change the same `wdbpack.bin`, so the one that goes in second built its
copy from the first one's. Taking the first off from under it the usual way,
handing the one on top what was under it, would leave the first one's records
in the game with its DLC files gone. Instead the ones on top come off, then
this one, then they go back on built over what's left, each holding the records
its own copy holds, found by its digest wherever a repack carried it
(`bridge._remove_rebuilt`). Cut short part way, what came off goes back on
over what's still there, and one whose copy can't be found stops the Remove
before anything moves. One on top that isn't a patcher's, a whole
`wdbpack.bin` of somebody's own, never had the other's records, so that's the
usual hand down. A drop of the one underneath again, an update or the same
version, is matched the way the install will match it, any case, and comes
out from under the same way first.

DLC files a Remove couldn't take, one held open by antivirus or a preview
pane, are listed under the mod's name until they can come off, and the full
revert takes them too. Worth getting's DLC Restoration on LR is both files, so
its Open page watches for the ones not in yet, by the mod id Nexus puts in each
name, and the row says how many are in until both are.

## Next to the HD Projects

A costume dropped in the drawer isn't HD. The survey reads each index with the
drawer's mods replaced by what's under them (`hd_state.without_file_mods`), so
one never badges a category as installed or reads as "in from outside this
app", repacked or not. An HD install leaves a model the drawer holds alone
(`hd_install.dropped_in`), clean backup or not, and names the mod and where to
remove it; the costume is what somebody put in on purpose. The voice copy skips
what the drawer put in, so a mod dropped into one voice set isn't carried into
the other as HD.

One exception for the badge only: the category the whole party is in
(Playable Characters, `mesh` in the manifest) reads as in when all six of them
are modded, each by its field model (`c0NN`) or its battle one (`c2NN`), from
the drawer or outside it (`hd_state.covered_categories`). By person and not by
the most-of bar `applied_categories` uses: XIII lists 34 ids for the six, so a
mod per person, which is how HD Models Plus comes, never got near it. All six,
because with five the HD Project still has the sixth to build. An install
leaves every model the drawer holds as it is anyway, and one modded outside it
is left as already modded. It's the survey's `covered`, apart from `applied`,
so the voice box and the debts still go by what the HD Project itself put in.
The row's tip names the drawer's mods, from the voice set the game is on, and
says modded in your game when there are none to name. Ticked, it builds
whatever of the category isn't in yet; a drop or a Remove in the drawer reads
the game again.

## Not yet

- Zone art. A zone's files live in their own archive, indexed from inside
  `white_img`, and that's two levels of the same thing. A whole LR zone archive
  that fits the game's index goes in over the game's ([Loose files](#loose-files));
  files for inside one, and zone indexes, are said and left out.
- A whole `white_scr` archive, or an index and archive for another release of
  the game, goes nowhere. The pairs a mod ships whole go in by what differs
  ([Whole archives](#whole-archives)).
- `.dds` and other loose art needs the mod's own tool to become an `.imgb`,
  unless it's a loader's data, which goes beside the exe with it.
- A `.bat` is played on a copy ([Any other installer .bat](#any-other-installer-bat)),
  but an installer `.exe` on its own isn't: it finds the game by itself and
  changes it where it sits, with nothing kept of what was under, so Remove
  couldn't take it back out. Its plain files or its Nova Chrysalia modpack go
  in instead. A patcher whose payload went in from its own zip is said not to
  be needed.
- Zone art a `.bat` builds (MJB's MODZONE) plays through, but a changed zone
  archive is said and left out, the same as zone files dropped by hand.
- A `.7z` or `.rar` inside a drop needs Windows' own tar, which Windows 10
  has had since 1803 and reads RAR on recent builds; one it can't open is the
  user's to unpack.
- Lightning Returns' archives are pairs like the others, `filelista`, `filelist2a`
  and `filelist_scra` for the English voice set, `v` for the Japanese, none for
  the Japanese release, carried as XIII's `u` and `c` so a voice set means the
  same in all three. The two patchers above have gone into a stand-in LR and
  matched what their exe writes byte for byte; a real LR install hasn't been
  tried.
- Remove goes by name, so an old version that's stuck for good (its originals
  gone, the Verify case) comes off with the new one when either is removed.
