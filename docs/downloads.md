# Downloads

## Sources

Most patches are bundled or come from GitHub releases and public direct
downloads, with no account. Apply never opens a browser and never asks for a
credential: anything that needs an account reports it on its own row and the
run carries on.

## Nexus

Nexus requires an account for downloads, so a Nexus-hosted mod can't be fully
one-click. The way around it is a mirror with the author's permission
([MIRRORS.md](../MIRRORS.md)):
`python tools/mirror.py <file> --game <id> --patch <id> --apply`.

| Account | Behaviour |
|---|---|
| Premium | Downloads resolve through the API with no clicks. |
| Free | After a run, every outstanding page opens at once; Mod Manager Download on each hands the file over via `nxm://`. |
| No key | Asks once for a personal API key (Account Settings > API Keys). |

Free accounts need the click because Nexus only issues the `key`/`expires`
pair on the website; the API returns 403 without them. The quota is 300
requests an hour (600 Premium), and the account tier is checked once per run.

`nxm://` is registered under HKCU (`patcher/nxm_registry.py`) and steps aside
if Vortex or MO2 already owns it.

## Sign in with Nexus (waiting on an app id)

The websocket SSO flow (`patcher/mods/nexus_sso.py`) is finished but needs an
application id only Nexus staff can issue. It's config, not code:

```
set FFXIII_NEXUS_APP_ID=your-id
{"nexus_app_id": "your-id"}      # in nexus_config.json
```

Until then the personal API key path is used.

## Worth getting: Open page and the Downloads watch

Mods the patcher can't fetch itself (Nexus-hosted, no redistribution) are under
the Mods drawer's Worth getting. Open page says which of the page's files to
get, opens it in the user's own browser, and watches Downloads
(`patcher/download_watch.py`):

- only while a row is waiting, only files created after the wait began
- a Nexus row only takes a file carrying its mod id the way Nexus names
  downloads; others match by name; "Mod (1).zip" counts as the same file
- a row needing two files (LR's DLC Restoration) watches for the ones not in
  yet, and two arriving together go in one after the other
- progress is a percentage when the size is known (the manifest's, or Nexus's
  with a key saved), otherwise KB/MB/GB
- a partial the browser is sitting on (Chrome holding a zip with an exe in it)
  is reported as waiting on Keep, not as stuck

What lands goes in through the drawer like a drop. See
[file-mods.md](file-mods.md).

## Large files

`patcher/download.py` streams: response to chunk to disk, archive member to
chunk to disk, so peak memory is one chunk. (Buffering a 400MB archive cost
+800MB RSS.)

- Partial downloads land in `.part` and resume with `Range`. A server that
  ignores `Range` is detected and the partial discarded.
- Free space is checked before writing; a zip's central directory gives the
  exact extracted size.
- Progress is in bytes, throttled to ~5 callbacks a second, the final one
  always delivered.
- Cancel is checked between chunks.
- Members that resolve outside the target are dropped.
- Redirects never go from https down to http.

## Archives Python can't open

`.rar` and `.7z` go through `bsdtar` (Windows' `tar.exe` since 10 1803; recent
builds read RAR). The result is checked for escapes and symlinks after
extraction, since bsdtar chose the paths. If that fails, the user extracts it
and picks the folder; a folder is scanned exactly like an archive.
