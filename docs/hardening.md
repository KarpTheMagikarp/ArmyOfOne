# Hardening

Failure modes found by auditing against the real code, each reproduced before
it was fixed and pinned by a test. Grouped by area.

## Detection

- Steam's own folder always counts as a library. The legacy flat
  `libraryfolders.vdf` only lists the extra libraries, so a game in Steam's
  default folder used to read as missing.
- Every library that claims the game is checked in turn. Steam leaves the
  `.acf` behind when a game folder is deleted by hand, and a stale one on C:
  mustn't shadow the real install on D:.
- A half written `.acf` is handled. `parse_vdf` raises `IndexError` at 34 of a
  normal manifest's 71 truncation points; unguarded, that took `scan()` and
  `--doctor` down.
- Steam is found through four registry locations plus the usual paths
  (`steam_locator.steam_roots`). When none work, the path line becomes a folder
  picker and the choice is remembered in `install_paths.json`.

## Stop and errors

- `Cancelled` subclasses `Exception`, so every apply path re-raises it before
  its generic handler. Otherwise Stop turned into "we'll open the mod page".
- `NotEnoughSpace`, truncated downloads and unreadable archives carry their own
  message to the user instead of being flattened into a generic failure.
- One cancel event per side operation, not one for the session, so a single
  Stop can't kill everything after it.

## Partial writes

- Every write goes through `fileops.write_atomic`: temp sibling, fsync where it
  matters, rename. An interrupted patch leaves the original intact.
- Archive members extract through a temp sibling and rename, and an abandoned
  extract removes what landed. A truncated proxy dll beside the exe would be
  loaded on the next launch with nothing in state to say it's there.
- The add-on sieve copies atomically. `shutil.copyfile` truncates before it
  fills, so a failed update left a zero length dll.

## Side doors

The drag and drop install, the add-on sieve and the CLI all reach the game
folder without `bridge.apply`, so each runs its own write probe before the
first byte. `mkdir(exist_ok=True)` proves nothing about a folder that already
exists.

## Deletes

Deletes clear the read-only attribute and report when they still fail
(`engine.remove_file`). A read-only `dinput8.dll` that silently survived a
"successful" removal is a second, invisible reason the game won't start.

## Encoding

- `novafix_config.write` uses `surrogateescape`, so a cp1251 `NovaFix.ini`
  round-trips byte for byte. `errors="replace"` had turned the user's comments
  into `EF BF BD`.
- `zipnames.windows_safe` escapes DOS device names (`NUL.asi` extracted into
  nowhere) and strips trailing dots and spaces.
- Zip member names flagged CP437 but really UTF-8 are recovered
  (`zipnames.repair`).
- Every file read or write carries an explicit encoding.
  `tests/test_hostile_environments.py` covers Cyrillic and CJK usernames,
  non-Latin library names and OEM codepage zips.

## Large files

The HD texture pack streams through `patcher/download.py`: resume via `Range`,
a free space check up front, byte progress, and cancel between chunks. See
[downloads.md](downloads.md).

## Staging

Staging lands on the destination's volume, never `%TEMP%` when the game is
elsewhere (`fileops.scratch_near`). A 4GB mod for a game on D: unpacks on D:.

## Records

`patcher.log`, `.state/` and `nexus_config.json` live in
`%LOCALAPPDATA%\FFXIIITrilogyPatcher`, not beside the exe, so a re-download
keeps its history. `portable.txt` beside the exe keeps them there instead.

## Checked and left alone

pywebview runs every `js_api` call on its own thread (`js_bridge_call` in
`webview/util.py` starts a `Thread`), so the bridge methods that do network or
extraction work inline don't block the UI thread.
