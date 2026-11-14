"""
The few files the mesh edits fopen() besides the models: a mod's mesh
catalogs (FFXIII_Ultimate_Models.bin, an add-on's own .dat) and the
component catalog the S command can write back into. Read out of the same
`files` the build was handed, in memory, with C's cursor semantics.
"""

SEEK_SET, SEEK_CUR, SEEK_END = 0, 1, 2


class CFile:
    def __init__(self, path: str, data: bytes, writable: bool = False):
        self.path = path
        self.data = bytearray(data)
        self.at = 0
        self.writable = writable
        self.dirty = False

    def read(self, size: int) -> bytes:
        size = max(size, 0)
        got = bytes(self.data[self.at:self.at + size])
        self.at += len(got)
        return got

    def readinto(self, mem, dst: int, size: int, count: int = 1) -> int:
        """fread(dst, size, count): whole items only, like C"""
        if size <= 0 or count <= 0:
            return 0
        items = min(count, max(len(self.data) - self.at, 0) // size)
        got = self.read(items * size)
        if got:
            mem.write(dst, got)
        # a short last item is still consumed from the stream
        tail = len(self.data) - self.at
        if items < count and 0 < tail < size:
            mem.write(dst + len(got), self.read(tail))
        return items

    def write(self, data) -> int:
        if not self.writable:
            return 0
        data = bytes(data)
        end = self.at + len(data)
        if end > len(self.data):
            self.data.extend(bytes(end - len(self.data)))
        self.data[self.at:end] = data
        self.at = end
        self.dirty = True
        return len(data)

    def seek(self, offset: int, whence: int = SEEK_SET) -> int:
        base = {SEEK_SET: 0, SEEK_CUR: self.at, SEEK_END: len(self.data)}[whence]
        if base + offset < 0:
            return -1
        self.at = base + offset
        return 0

    def tell(self) -> int:
        return self.at

    def gets(self, size: int):
        """fgets: up to size - 1 chars, through the newline; None at the end"""
        if self.at >= len(self.data) or size <= 1:
            return None
        end = self.data.find(b"\n", self.at, self.at + size - 1)
        stop = end + 1 if end >= 0 else min(self.at + size - 1, len(self.data))
        got = bytes(self.data[self.at:stop])
        self.at = stop
        return got


def fopen(b, path: str, mode: str = "rb"):
    """the file at `path` relative to the mod's folder, None when it isn't there"""
    data = b.files.read(win32_name(path))
    if data is None:
        return None
    f = CFile(path, data, writable="+" in mode or "w" in mode)
    if f.writable:
        # what got written back is the build's to hand on, the way the exe
        # leaves a changed catalog on disk for the next run to read
        b.__dict__.setdefault("written", {})[path] = f
    return f


def win32_name(path: str) -> str:
    """
    Windows drops trailing spaces and dots off a path's last part, so
    "data\\Vanille_TL.dat " (the add-on's generated recipes end their lines
    in a space) opens Vanille_TL.dat. The parts before it keep theirs
    """
    cut = max(path.rfind("\\"), path.rfind("/")) + 1
    trimmed = path[cut:].rstrip(" .")
    return path[:cut] + trimmed if trimmed else path
