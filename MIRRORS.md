# Mirrors

Every patch that installs in one click does so because its file ships with the
patcher or is fetchable without an account. Those rows always finish on their
own.

The rows that ask the user for anything are the ones still pointed at Nexus.
That is not a UI problem and no sign-in flow fixes it: Nexus refuses download
links to non-Premium accounts unless the request carries a `key`/`expires`
pair that only its website mints. The way a Nexus-hosted patch becomes
one-click is to stop fetching it from Nexus.

So: ask the author, host the file, add two keys.

## Doing it

1. Ask. Template below. Nexus profiles have a Message button; most authors
   also list a Discord.
2. On a yes, upload the file to a GitHub release on this repo.
3. Generate the manifest entry:

   ```
   python tools/mirror.py ff13-audio.zip --game ffxiii \
       --patch install_cutscene_audio --url https://github.com/<you>/<repo>/releases/download/mirrors/ff13-audio.zip --apply
   ```

That patch is now a one-click install for everyone, free or Premium. The
SHA-256 is checked on download, so a swapped or truncated file fails loudly
instead of installing.

## Bundled with permission

| Mod | Author | Where |
|---|---|---|
| NovaFix | Kartin8 | `assets/mods/novafix/` |

## Bundled under permissive licences

| What | Author | Where | Licence |
|---|---|---|---|
| ReShade 6.8.0 | crosire | `assets/mods/reshade/d3d9.dll` | BSD-3-Clause |
| SMAA, LumaSharpen, Curves, Vibrance | CeeJay.dk (SweetFX), SMAA by Jorge Jimenez et al. | `assets/mods/reshade/reshade-shaders/` | MIT, SMAA's MIT-style |
| ReShade.fxh, ReShadeUI.fxh | crosire (reshade-shaders) | the same | CC0 |

A tool and the shader libraries ReShade presets routinely ship with it, not
somebody's mod, so there's no ask to owe. The preset they run, Fabula-Lous
Shader, XIII-2's version of it, and its grade, `FabulaLous.fx`, are this
project's own.

Every one of these has its SHA-256 pinned in the manifest and checked before
anything is written. `tools/pin_bundled.py` rewrites those pins after a file is
replaced, and `--check` fails if any has drifted.

NovaFix is the reason the pin rule reads the way it does. While it was
user-supplied a pinned hash refused every build but the one whose hash we
recorded, which is exactly what made a perfectly good download fail on a second
machine. Now that it ships here, the pin is us vouching for what we send, and
shape validation still runs underneath it so a corrupted bundle gets a sentence
that names the problem rather than a hash mismatch.

## Still on Nexus

| Game | Patch | Mod | Author | Asked | Reply |
|---|---|---|---|---|---|
| LR | DLC Restoration | [mods/23](https://www.nexusmods.com/lightningreturnsfinalfantasy13/mods/23) | Krisan Thyme | | |

**This is the only row left where asking would convert anything, and that is
worth being precise about.**

A mirror removes the Nexus gate. It does not remove an installer. Both HD packs
and XIII-2's console content restore end at `NEEDS_INSTALLER` or `MANUAL`
whichever way the bytes arrive, because the mod ships a `.bat` that asks
questions and wants tens of GB of scratch space. Premium account, personal API
key, mirror on our own release page: same ending, every time. The Nexus gate was
never the binding constraint on those.

So they are no longer offered in the Apply list. They are `kind: "extra"` in
`ui/jobs.json`, which surfaces them in the Mods drawer with a link to the page,
where going and getting something is the expected thing rather than a job that
failed. `tests/test_engine_coverage.py` fails if an installer-backed or
manual-only job is ever offered in Apply again.

LR's DLC Restoration is different in the way that matters: it is plain files,
no installer. Today it completes through the acquisition flow (Apply opens the
page, the user presses download, the watcher catches the file and installs it),
and a mirror would make it a true one-click with no website visit at all. That
is the whole remaining prize, and it is one message to one author.

## Mirroring would not help these

Still shipped, still linked from the Mods drawer, deliberately not on the list
above. Recorded here so nobody spends an afternoon working out why we never
asked.

| Game | Mod | Why a mirror changes nothing |
|---|---|---|
| XIII | HD Project, [mods/17](https://www.nexusmods.com/finalfantasy13/mods/17) | ships a `.bat` installer that asks questions and needs tens of GB of scratch space |
| XIII | HD Models Plus, [mods/11](https://www.nexusmods.com/finalfantasy13/mods/11) | the user's download, caught by Worth getting's Open page or dropped on the Mods drawer: the patcher runs its model tool on the game's own originals, never its `.bat`, and nothing of it ships here. Its Nova Chrysalia versions go in as they are |
| all three | HD Fonts and GUI, [XIII mods/67](https://www.nexusmods.com/finalfantasy13/mods/67), [XIII-2 mods/40](https://www.nexusmods.com/finalfantasyxiii2/mods/40), [LR mods/33](https://www.nexusmods.com/lightningreturnsfinalfantasy13/mods/33) | its permissions don't allow re-uploading it, so there's nothing to ask for: the user's download, caught by Worth getting's Open page, goes in beside the exe that loads it, and nothing of it ships here |
| LR | Savior's Patch, [mods/36](https://www.nexusmods.com/lightningreturnsfinalfantasy13/mods/36) | left out of the patcher on purpose, after shipping bundled under its GPL-3.0 for a while: the user's download, caught by Worth getting's Open page, goes in beside `LRFF13.exe`, and nothing of it ships here |
| XIII | Clean ReShade Preset, [mods/15](https://www.nexusmods.com/finalfantasy13/mods/15) | its permissions don't allow re-uploading it or using its assets at all, so there's nothing to ask for: the Fabula-Lous Shader Extra ships this patcher's own preset instead |
| XIII-2 | FF XIII-2 HD, [mods/1](https://www.nexusmods.com/finalfantasyxiii2/mods/1) | same installer |
| XIII-2 | Console content restore | ends in its own `.bat`, run in the game folder, which Apply can never finish for it |

Driving those installers automatically would mean scripting prompts that change
between releases, on a machine that may run out of disk halfway through, doing
something the author deliberately made interactive. That is the line at
`engine.py`'s `runs_installer` branch: downloading unattended is fine, running
it unattended is not.

`tests/test_nexus_source.py::test_no_new_required_job_depends_on_a_nexus_account`
holds a list of required jobs that still need outside help. It is written so
the list can only shrink; remove an entry when its mirror lands.

## Request template

> Subject: Permission to mirror [MOD] in ArmyOfOne, a free FFXIII trilogy patcher
>
> Hi [AUTHOR],
>
> I've built a free, open source patcher for the Steam releases of FFXIII,
> XIII-2 and Lightning Returns. It detects the install and applies the fixes
> the ports need in one click: [REPO URL].
>
> I'd like to include [MOD]. Nexus only allows automatic downloads for
> Premium accounts, so as things stand the tool has to send people to your mod
> page to download it manually, which is a worse experience than the rest of
> the tool and means a lot of them will skip it.
>
> Would you be willing to let me mirror the file so it installs directly? If
> so:
>
> - Your name and a link to the Nexus page are shown in the tool and the
>   readme, and the mod page stays the canonical source.
> - I'll pin a specific version and verify it by checksum, so nothing gets
>   altered in transit.
> - I'll update the mirror whenever you release, or drop it immediately if you
>   ever want it taken down, no questions.
>
> Completely understand if you'd rather it stayed on Nexus only. Either way,
> thanks for making it. [SPECIFIC THING THE MOD FIXES] is one of the biggest
> problems with these ports.
>
> [YOUR NAME]

Mention endorsements, download counts, or anything that reads as leverage and
you will get a no. The pitch is that more people end up with their mod
installed and working.
