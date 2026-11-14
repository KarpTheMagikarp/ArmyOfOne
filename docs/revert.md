# Revert

```
ArmyOfOne.exe ffxiii2 --revert
python main.py ffxiii2 --revert
```

Takes off everything the patcher applied to that game: installed files, the
4GB flag, archive injections (from their span journals) and, on XIII and
XIII-2, the HD Project (from the backup taken before its first install). Drawer
mods, a retired Leviathan's Tears install and XIII-2 HD can be in together in
any order; one Revert takes them all off.

## Rules

- Only what the patcher recorded installing, and only while it's still the
  file it installed. Anything else stays.
- A file of the user's that Apply found under the same name (a ReShade
  `d3d9.dll`, an ASI loader's `dinput8.dll`) was set aside as `.backup` with
  its hash recorded, and Revert puts back exactly that file.
- Archive injections follow the same rule per entry. A revert that couldn't
  leave an archive agreeing with its index refuses before writing anything.
- Every file in the HD backup is hashed before any of it goes back, both voice
  sets. A backup the game hasn't been written from since it was taken is left
  alone, so a Steam update isn't undone.
- Patches come off in reverse order, since later ones can sit on earlier ones.

## The undo store

`.patcher-undo` in the game folder. Files can be copied out, but renaming,
moving or deleting anything in it breaks the matching. A missing or mismatched
file makes Revert say what's wrong and how to get to a clean game (usually a
Steam Verify), and the HD install refuses to go on top of a backup that
doesn't check out.

## From the release exe

The exe is windowed, so cmd and PowerShell return the prompt immediately. It
borrows the parent console to say what it's reverting
(`access.attach_parent_console`), releases it before anything moves so closing
the terminal can't stop a revert halfway, and shows the result in a message
box.
