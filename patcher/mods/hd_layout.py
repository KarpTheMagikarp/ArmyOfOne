"""
Where each game keeps what the HD installs touch.

XIII rebuilds its white_scr pair for characters and keeps white_img for the
zone indexes. XIII-2 has no scr pair: characters and zone indexes both live in
white_img, so one archive is rebuilt and imported into. Revert and the backup
code serve both games, so they ask the install folder which one it is.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Layout:
    game: str             # the manifest id, which is also what --revert takes
    title: str
    data: str             # white_data or alba_data
    chr_index: str        # the pair the character pass rebuilds, {} is the voice set
    chr_archive: str
    sealed: bool          # the game encrypts its indexes, so ff13tool gets a decrypted copy

    def sys_dir(self, game_dir) -> Path:
        return Path(game_dir) / self.data / "sys"

    def zone_dir(self, game_dir) -> Path:
        return Path(game_dir) / self.data / "zone"

    def pair(self, game_dir, variant: str) -> list:
        sys_dir = self.sys_dir(game_dir)
        return [sys_dir / self.chr_index.format(variant), sys_dir / self.chr_archive.format(variant)]

    @property
    def revert(self) -> str:
        return f"Revert (ArmyOfOne.exe {self.game} --revert, from the patcher's folder)"


XIII = Layout("ffxiii", "Final Fantasy XIII", "white_data",
              "filelist_scr{}.win32.bin", "white_scr{}.win32.bin", sealed=False)
XIII2 = Layout("ffxiii2", "Final Fantasy XIII-2", "alba_data",
               "filelist{}.win32.bin", "white_img{}.win32.bin", sealed=True)


def layout_of(game_dir) -> Layout:
    # a game folder has one of the two, and XIII's is the default because every
    # caller that predates XIII-2 was written against it
    return XIII2 if (Path(game_dir) / XIII2.data).is_dir() else XIII
